"""ATL-2 grader: the orders consumer's partial-batch failures, idempotent writes and exact amounts.

- test_lambda_and_esm_exist and test_event_source_mapping_declares_report_batch_item_failures:
  the consumer's configuration and its event source mapping.
- test_end_to_end_fifo_group_ordering and test_end_to_end_duplicate_post_is_idempotent send each
  message straight to orders.fifo with the grader-only identity instead of POSTing it through
  orders-api (that API is ATL-1's). The resubmission in the duplicate test uses a new
  deduplication id, as a resubmission after SQS's five-minute FIFO deduplication window would; with
  the same id SQS would drop the copy and the consumer's idempotency would never be exercised.
- The partial batch response and the acknowledgement of an already-stored order, checked by
  invoking the deployed function with an SQS-shaped event (grader-only identity); the amount
  stored exactly as sent; the deployed code is what the repository builds.
"""

import json
import os
import sys
import time
import uuid
from decimal import Decimal

import pytest

sys.path.insert(0, os.environ["ATLAS_LIB"])
import atlas_grader as ag  # noqa: E402

MAIN_QUEUE = "orders.fifo"
DLQ_QUEUE = "orders-dlq.fifo"
LAMBDA_FUNC = "orders-consumer"
DDB_TABLE = "orders"
QUEUE_ARN = f"arn:aws:sqs:{ag.REGION}:000000000000:{MAIN_QUEUE}"


@pytest.fixture(scope="module")
def lmb():
    return ag.client("lambda")


@pytest.fixture(scope="module")
def ddb():
    return ag.client("dynamodb")


@pytest.fixture(scope="module")
def sqs_v():
    return ag.client("sqs", verifier=True)


@pytest.fixture(scope="module")
def lmb_v():
    return ag.client("lambda", verifier=True)


@pytest.fixture(scope="module")
def queues(sqs_v):
    main_url = sqs_v.get_queue_url(QueueName=MAIN_QUEUE)["QueueUrl"]
    dlq_url = sqs_v.get_queue_url(QueueName=DLQ_QUEUE)["QueueUrl"]
    # Start from empty queues, so nothing the agent sent while
    # working counts against the checks below.
    ag.drain(sqs_v, main_url)
    ag.drain(sqs_v, dlq_url)
    return {"main": main_url, "dlq": dlq_url}


def _send(sqs_v, queues, body, dedup_id):
    sqs_v.send_message(
        QueueUrl=queues["main"],
        MessageBody=json.dumps(body) if not isinstance(body, str) else body,
        MessageGroupId=body["merchant_id"],
        MessageDeduplicationId=dedup_id,
    )


def _wait_row(ddb, order_id, seconds=45, merchant=None):
    deadline = time.time() + seconds
    row = None
    while time.time() < deadline:
        row = ddb.get_item(TableName=DDB_TABLE, Key={"order_id": {"S": order_id}}).get("Item")
        if row and (merchant is None or row.get("merchant_id", {}).get("S") == merchant):
            return row
        time.sleep(2)
    return row


def _dlq_depth(sqs_v, queues):
    attrs = sqs_v.get_queue_attributes(QueueUrl=queues["dlq"], AttributeNames=["ApproximateNumberOfMessages"])
    return int(attrs["Attributes"]["ApproximateNumberOfMessages"])


def _record(body, message_id=None):
    message_id = message_id or str(uuid.uuid4())
    return {
        "messageId": message_id,
        "receiptHandle": f"grader-{message_id}",
        "body": body if isinstance(body, str) else json.dumps(body),
        "attributes": {"ApproximateReceiveCount": "1", "SentTimestamp": str(int(time.time() * 1000)),
                       "MessageGroupId": "grader", "MessageDeduplicationId": message_id},
        "messageAttributes": {},
        "md5OfBody": "",
        "eventSource": "aws:sqs",
        "eventSourceARN": QUEUE_ARN,
        "awsRegion": ag.REGION,
    }


def _invoke(lmb_v, records):
    resp = lmb_v.invoke(FunctionName=LAMBDA_FUNC, Payload=json.dumps({"Records": records}).encode())
    payload = resp["Payload"].read()
    assert not resp.get("FunctionError"), (
        f"{LAMBDA_FUNC} raised instead of answering ({resp.get('FunctionError')}): {payload[:500]!r}. "
        f"An unprocessable message must be reported in the response, not raised."
    )
    try:
        return json.loads(payload or b"null")
    except ValueError:
        pytest.fail(f"{LAMBDA_FUNC} returned a non-JSON response: {payload[:300]!r}")


# --- configuration and end-to-end checks ----------------------------------------------------------

def test_lambda_and_esm_exist(lmb):
    """Lambda and ESM."""
    cfg = lmb.get_function(FunctionName=LAMBDA_FUNC)["Configuration"]
    assert cfg["Runtime"] == "python3.11"
    assert cfg["Handler"] == "handler.lambda_handler"
    variables = cfg.get("Environment", {}).get("Variables", {})
    assert "AWS_ACCESS_KEY_ID" not in variables
    assert "AWS_SECRET_ACCESS_KEY" not in variables
    mappings = lmb.list_event_source_mappings(FunctionName=LAMBDA_FUNC)[
        "EventSourceMappings"
    ]
    assert any(
        ":sqs:" in m.get("EventSourceArn", "") for m in mappings
    ), f"no SQS ESM on {LAMBDA_FUNC}: {mappings}"


def test_event_source_mapping_declares_report_batch_item_failures(lmb):
    """ESM reports batch failures."""
    mappings = lmb.list_event_source_mappings(FunctionName=LAMBDA_FUNC)[
        "EventSourceMappings"
    ]
    sqs_mappings = [
        m for m in mappings if ":sqs:" in m.get("EventSourceArn", "")
    ]
    assert sqs_mappings, f"no SQS ESM on {LAMBDA_FUNC}"
    frt = sqs_mappings[0].get("FunctionResponseTypes") or []
    assert "ReportBatchItemFailures" in frt, (
        f"ESM {sqs_mappings[0].get('UUID')} is missing "
        f"FunctionResponseTypes=['ReportBatchItemFailures']. Without "
        f"it, returning `batchItemFailures` is silently ignored and "
        f"the whole batch is retried on any one failure."
    )


# --- new: the repository is what runs -----------------------------------------------------------

def test_deployed_code_matches_repository(lmb):
    """The code running in orders-consumer is what deploy/deploy.py builds from services/orders_consumer/."""
    source = ag.repo() / "services" / "orders_consumer"
    assert (source / "handler.py").is_file(), f"{source}/handler.py is missing from the repository"
    expected = ag.code_sha256(ag.package_dir(source))
    deployed = lmb.get_function_configuration(FunctionName=LAMBDA_FUNC)["CodeSha256"]
    assert deployed == expected, (
        f"{LAMBDA_FUNC} runs code (CodeSha256 {deployed}) that is not what the repository's "
        f"services/orders_consumer/ packages to ({expected}); deploy the committed code with "
        f"`python3 deploy/deploy.py --component orders_core`."
    )


# --- new: the partial batch response, checked on the deployed function --------------------------

def test_partial_batch_response_reports_only_unprocessable_messages(lmb_v, ddb):
    merchant = f"m-pb-{uuid.uuid4().hex[:6]}"
    good_a = {"order_id": f"o-pb-a-{uuid.uuid4().hex[:8]}", "merchant_id": merchant, "amount": 5}
    good_b = {"order_id": f"o-pb-b-{uuid.uuid4().hex[:8]}", "merchant_id": merchant, "amount": 7}
    bad = _record("{this is not json")
    response = _invoke(lmb_v, [_record(good_a), bad, _record(good_b)])
    assert response == {"batchItemFailures": [{"itemIdentifier": bad["messageId"]}]}, (
        f"for a batch of two valid orders and one unparseable message the response must be "
        f"{{'batchItemFailures': [{{'itemIdentifier': <the bad messageId>}}]}}; got {response!r}"
    )
    for order in (good_a, good_b):
        row = ddb.get_item(TableName=DDB_TABLE, Key={"order_id": {"S": order["order_id"]}}).get("Item")
        assert row and row.get("merchant_id", {}).get("S") == merchant, (
            f"valid order {order['order_id']} in the same batch as a bad message was not stored: {row!r}"
        )


def test_redelivered_order_is_acknowledged_without_change(lmb_v, ddb):
    order = {"order_id": f"o-rd-{uuid.uuid4().hex[:8]}", "merchant_id": f"m-rd-{uuid.uuid4().hex[:6]}",
             "amount": 42}
    first = _invoke(lmb_v, [_record(order)])
    assert first == {"batchItemFailures": []}, f"first delivery of a valid order: expected no failures, got {first!r}"
    row = ddb.get_item(TableName=DDB_TABLE, Key={"order_id": {"S": order["order_id"]}}).get("Item")
    assert row, f"first delivery of {order['order_id']} was not stored"
    time.sleep(2)
    again = _invoke(lmb_v, [_record(order)])
    assert again == {"batchItemFailures": []}, (
        f"a redelivered order that is already stored must be acknowledged as processed; got {again!r}"
    )
    row2 = ddb.get_item(TableName=DDB_TABLE, Key={"order_id": {"S": order["order_id"]}}).get("Item")
    assert row2 == row, f"redelivery changed the stored row: {row!r} -> {row2!r}"


# --- end to end through orders.fifo ---------------------------------------------------------------

def test_end_to_end_row_is_stored_unchanged(sqs_v, queues, ddb):
    order_id = f"o-amt-{uuid.uuid4().hex[:8]}"
    merchant = f"m-amt-{uuid.uuid4().hex[:6]}"
    # The amount is sent as the JSON number 19.99, as clients send it.
    body = f'{{"order_id": "{order_id}", "merchant_id": "{merchant}", "amount": 19.99}}'
    sqs_v.send_message(QueueUrl=queues["main"], MessageBody=body, MessageGroupId=merchant,
                       MessageDeduplicationId=order_id)
    row = _wait_row(ddb, order_id, merchant=merchant)
    assert row is not None, f"no row for order_id={order_id} within 45s of sending it to {MAIN_QUEUE}"
    assert row.get("merchant_id", {}).get("S") == merchant
    stored = row.get("amount", {}).get("N")
    assert stored is not None and Decimal(stored) == Decimal("19.99"), (
        f"order sent with amount 19.99 was stored with amount {row.get('amount')!r}"
    )
    assert _dlq_depth(sqs_v, queues) == 0, f"{DLQ_QUEUE} has messages after a valid order"


def test_end_to_end_fifo_group_ordering(sqs_v, queues, ddb):
    """FIFO per-group ordering."""
    merchant = f"m-ord-{uuid.uuid4().hex[:6]}"
    order_a = f"o-ord-a-{uuid.uuid4().hex[:6]}"
    order_b = f"o-ord-b-{uuid.uuid4().hex[:6]}"

    _send(sqs_v, queues, {"order_id": order_a, "merchant_id": merchant, "amount": 1}, order_a)
    _send(sqs_v, queues, {"order_id": order_b, "merchant_id": merchant, "amount": 2}, order_b)

    deadline = time.time() + 45
    row_a = row_b = None
    while time.time() < deadline:
        if not row_a:
            row_a = ddb.get_item(
                TableName=DDB_TABLE, Key={"order_id": {"S": order_a}}
            ).get("Item")
        if not row_b:
            row_b = ddb.get_item(
                TableName=DDB_TABLE, Key={"order_id": {"S": order_b}}
            ).get("Item")
        if row_a and row_b:
            break
        time.sleep(2)
    assert row_a and row_b, (
        f"FIFO group ordering test: one or both rows missing "
        f"(A={bool(row_a)}, B={bool(row_b)}) within 45s. Group="
        f"{merchant!r}."
    )
    ts_a = int(row_a["created_at"]["N"])
    ts_b = int(row_b["created_at"]["N"])
    assert ts_a <= ts_b, (
        f"FIFO per-group ordering violated: order A created_at={ts_a} > "
        f"order B created_at={ts_b} despite being posted earlier. "
        f"Either MessageGroupId is not derived per-merchant or the "
        f"consumer is processing out of order."
    )

    assert _dlq_depth(sqs_v, queues) == 0, (
        f"{DLQ_QUEUE} has messages after two successful ordered POSTs."
    )


def test_end_to_end_duplicate_post_is_idempotent(sqs_v, queues, ddb):
    """Duplicate POST idempotent."""
    order_id = f"o-dup-{uuid.uuid4().hex[:8]}"
    merchant = f"m-dup-{uuid.uuid4().hex[:6]}"
    body = {"order_id": order_id, "merchant_id": merchant, "amount": 11}

    _send(sqs_v, queues, body, order_id)

    deadline = time.time() + 30
    row = None
    while time.time() < deadline:
        row = ddb.get_item(
            TableName=DDB_TABLE, Key={"order_id": {"S": order_id}}
        ).get("Item")
        if row:
            break
        time.sleep(2)
    assert row, f"first row never materialized for {order_id}"
    first_created_at = row["created_at"]["N"]

    time.sleep(2)
    # A resubmission after the FIFO deduplication window: same body, new deduplication id.
    _send(sqs_v, queues, body, f"{order_id}-resubmitted")

    time.sleep(8)

    row2 = ddb.get_item(
        TableName=DDB_TABLE, Key={"order_id": {"S": order_id}}
    ).get("Item")
    assert row2, "row vanished between POSTs"
    assert row2["created_at"]["N"] == first_created_at, (
        f"duplicate POST overwrote the first row. Idempotency via "
        f"ConditionExpression is not in place."
    )

    assert _dlq_depth(sqs_v, queues) == 0, (
        f"{DLQ_QUEUE} has messages after a duplicate POST. A correct "
        f"handler catches ConditionalCheckFailedException and treats "
        f"the duplicate as success."
    )
