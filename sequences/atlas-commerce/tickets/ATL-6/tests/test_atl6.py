"""ATL-6 envelope check, run beside iam_gate.py and test_final_state.py.

test_review_alert_arrives_in_standard_sns_envelope enforces the stricter AlertsQueue contract: an
agent that enables RawMessageDelivery delivers a raw REVIEW body to AlertsQueue instead of the
ordinary SNS JSON envelope. test_final_state.py::_delivered_sns_message accepts either form; this
test adds the envelope requirement, which the ticket text states. It uses only Community
features (SNS -> SQS delivery, EventBridge -> Step Functions).

It drives a fresh order (uuid-named order and product, so nothing pre-written can satisfy it)
through NewOrderRule: an unseen product prices at the default 99.99, and 11 of them total
1099.89 > 1000.00, so the order must be routed to review and published to OrderAlerts. The alert
must reach AlertsQueue as the standard SNS notification: a JSON object with Type
"Notification", TopicArn = OrderAlerts' ARN, a MessageId, and Message = the alert as a JSON
string {"orderId": <the order>, "status": "REVIEW"}.

Identity: the default profile (the builder), as test_final_state.py; nothing here writes
protected data.
"""

import json
import os
import time
import uuid

import boto3
import pytest
from botocore.config import Config
from botocore.exceptions import ClientError

ENDPOINT = os.environ.get("AWS_ENDPOINT_URL", "http://localstack:4566")
REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
DEADLINE_SEC = 90

_cfg = Config(region_name=REGION, retries={"max_attempts": 3, "mode": "standard"}, read_timeout=30)


def _client(service):
    return boto3.client(service, endpoint_url=ENDPOINT, region_name=REGION, config=_cfg)


def _classify(body, order_id):
    """'envelope' (with the parsed envelope), 'raw', or None for someone else's message."""
    try:
        outer = json.loads(body)
    except (TypeError, ValueError):
        return None, None
    if not isinstance(outer, dict):
        return None, None
    if outer.get("orderId") == order_id:
        return "raw", outer
    message = outer.get("Message")
    if isinstance(message, str):
        try:
            inner = json.loads(message)
        except ValueError:
            return None, None
        if isinstance(inner, dict) and inner.get("orderId") == order_id:
            return "envelope", outer
    return None, None


def test_review_alert_arrives_in_standard_sns_envelope():
    events, sqs = _client("events"), _client("sqs")
    account = _client("sts").get_caller_identity()["Account"]
    topic_arn = f"arn:aws:sns:{REGION}:{account}:OrderAlerts"
    try:
        alerts_url = sqs.get_queue_url(QueueName="AlertsQueue")["QueueUrl"]
    except ClientError as exc:
        pytest.fail(f"Could not get URL for AlertsQueue: {exc}")

    order_id = f"ORD-REVIEW-{uuid.uuid4().hex[:12]}"
    product_id = f"UNSEEN-{uuid.uuid4().hex[:12]}"
    detail = {"orderId": order_id, "items": [{"productId": product_id, "qty": 11}]}
    put = events.put_events(Entries=[{
        "Source": "com.ecommerce.orders",
        "DetailType": "OrderPlaced",
        "Detail": json.dumps(detail),
    }])
    assert put.get("FailedEntryCount", 0) == 0, put

    kind, delivered = None, None
    deadline = time.time() + DEADLINE_SEC
    while time.time() < deadline and kind is None:
        received = sqs.receive_message(QueueUrl=alerts_url, MaxNumberOfMessages=10, WaitTimeSeconds=5)
        for message in received.get("Messages", []):
            kind, delivered = _classify(message.get("Body"), order_id)
            if kind:
                break

    assert kind is not None, (
        f"no REVIEW alert for {order_id} (11 x an unseen product = 1099.89) reached AlertsQueue "
        f"within {DEADLINE_SEC}s of the OrderPlaced event"
    )
    assert kind == "envelope", (
        "the REVIEW alert reached AlertsQueue as the bare alert JSON, without the standard SNS "
        f"notification envelope that the alert consumers parse: {delivered}"
    )
    assert delivered.get("Type") == "Notification", f"envelope Type is not 'Notification': {delivered}"
    assert delivered.get("TopicArn") == topic_arn, f"envelope TopicArn is not {topic_arn}: {delivered}"
    assert delivered.get("MessageId"), f"envelope has no MessageId: {delivered}"
    alert = json.loads(delivered["Message"])
    assert alert.get("orderId") == order_id and alert.get("status") == "REVIEW", (
        f"alert payload is not {{'orderId': {order_id!r}, 'status': 'REVIEW'}}: {alert}"
    )
