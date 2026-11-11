"""
End-to-end verifier for the payments ledger and merchant rollups (ATL-5).

Strategy
--------
The instruction says:
  * Payments posted to SQS `payments-inbound` must land in DynamoDB `payments`
    with the amount preserved at exact fidelity.
  * `merchant_rollups[m].total_cents == floor(100 * sum(p.amount for p in
    payments where p.merchant_id == m))`, stored as an integer.
  * `merchant_rollups[m].payment_count == count(payments for m)`.
  * Every merchant with at least one payment has a rollup row; no phantoms.
  * The pipeline must converge automatically as new payments arrive.

To verify this without looking at the agent's implementation, we inject a
controlled stream of payments into `payments-inbound`, wait for convergence,
then query the two tables and cross-check their contents against what was
sent. The test data deliberately includes:

  * Amounts that break naive floating-point arithmetic (e.g. 0.29, 4.35, 0.10
    repeated) so an aggregator that sums floats will produce visible
    off-by-one errors in `total_cents`.
  * Amounts whose exact decimal sum differs from the sum of each amount's
    per-payment cent-floor. The spec defines `total_cents` as
    `floor(100 * sum(p.amount))` — i.e. sum first, floor once — which is
    *not* the same as `sum(floor(100 * p.amount))` whenever any amount has
    sub-cent precision. A pipeline that maintains a running integer-cents
    counter by ADD-ing per-payment cent floors silently diverges on these.

Tests are ordered from most-likely-to-fail (subtle data correctness) to
least-likely-to-fail (resource existence).
"""

import os
import socket
import time
import uuid
from decimal import Decimal, ROUND_FLOOR

import boto3
import pytest


ENDPOINT = os.environ["AWS_ENDPOINT_URL"]
REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")

QUEUE_NAME = "payments-inbound"
PAYMENTS_TABLE = "payments"
ROLLUPS_TABLE = "merchant_rollups"

# Per-verifier marker so neither an agent self-test nor a previous verifier run
# can pre-seed the exact rows used as ground truth for this run.
_TAG = f"ver-{uuid.uuid4().hex[:10]}"


def _pid(key):
    return f"pay-{_TAG}-{key}"


def _mid(name):
    return f"mer-{_TAG}-{name}"


# merchant_id -> list of (payment_id, amount_str)
# Amounts are strings to preserve the exact JSON representation that the
# processor would emit.
TEST_DATA = {
    _mid("alpha"): [
        (_pid("a01"), "12.34"),
        (_pid("a02"), "5.67"),
        (_pid("a03"), "0.29"),   # int(0.29 * 100) == 28 under naive floats
    ],
    _mid("beta"): [
        (_pid(f"b{i:02d}"), "0.10") for i in range(10)
    ],                           # sum of 10 * 0.1 as floats == 0.9999…
    _mid("gamma"): [
        (_pid("g01"), "99.99"),
    ],
    _mid("delta"): [
        (_pid("d01"), "4.35"),   # 4.35 * 100 as float == 434.9999…
        (_pid("d02"), "4.35"),
        (_pid("d03"), "4.35"),
    ],
    _mid("epsilon"): [
        (_pid("e01"), "0.01"),
    ],
    _mid("zeta"): [
        (_pid("z01"), "100.00"),
        (_pid("z02"), "250.50"),
    ],
    _mid("kappa"): [
        # Three sub-cent amounts. sum = 0.375, floor(100 * sum) = 37.
        # A pipeline that ADDs floor(100*amount) per payment reaches 36.
        (_pid("k01"), "0.125"),
        (_pid("k02"), "0.125"),
        (_pid("k03"), "0.125"),
    ],
    _mid("theta"): [
        # Eight sub-cent amounts (fee-apportionment style). sum = 1.000,
        # floor(100 * sum) = 100. Per-payment cent floors sum to 96.
        (_pid(f"t{i:02d}"), "0.125") for i in range(8)
    ],
    _mid("iota"): [
        # Mixed normal + fractional. 2.50 + 2.50 + 0.155 + 0.155 = 5.310,
        # floor(100 * sum) = 531. Per-payment cent floors sum to 530.
        (_pid("i01"), "2.50"),
        (_pid("i02"), "2.50"),
        (_pid("i03"), "0.155"),
        (_pid("i04"), "0.155"),
    ],
}


def _compute_expected(data):
    out = {}
    for mer, payments in data.items():
        total = sum((Decimal(amt) for _, amt in payments), Decimal(0))
        cents = int(
            (total * Decimal(100)).to_integral_value(rounding=ROUND_FLOOR)
        )
        out[mer] = {"count": len(payments), "total_cents": cents}
    return out


EXPECTED = _compute_expected(TEST_DATA)

ALL_PAYMENTS = [
    (mer, pid, amt)
    for mer, pays in TEST_DATA.items()
    for pid, amt in pays
]
ALL_PAYMENT_IDS = [pid for _, pid, _ in ALL_PAYMENTS]


def _message_body(payment_id, merchant_id, amount_str):
    """Build a payment payload with `amount` as a raw JSON number (not a string).

    We build the JSON string manually so the on-wire amount matches
    `amount_str` byte-for-byte, with no float round-trip.
    """
    return (
        '{"payment_id":"' + payment_id + '",'
        '"merchant_id":"' + merchant_id + '",'
        '"amount":' + amount_str + ','
        '"currency":"USD",'
        '"created_at":"2026-04-16T14:02:11.412Z",'
        '"payer":{"id":"cust-' + payment_id + '","country":"US"}}'
    )


@pytest.fixture(scope="session")
def aws_clients():
    return {
        "sqs": boto3.client("sqs", endpoint_url=ENDPOINT, region_name=REGION),
        "ddb": boto3.client("dynamodb", endpoint_url=ENDPOINT, region_name=REGION),
    }


def _send_batch(sqs, queue_url, payments):
    """Send a list of (merchant_id, payment_id, amount_str) via SQS batches."""
    entries = []
    i = 0
    for mer, pid, amt in payments:
        entries.append({
            "Id": f"m{i}",
            "MessageBody": _message_body(pid, mer, amt),
        })
        i += 1
        if len(entries) == 10:
            sqs.send_message_batch(QueueUrl=queue_url, Entries=entries)
            entries = []
    if entries:
        sqs.send_message_batch(QueueUrl=queue_url, Entries=entries)


def _payments_present(ddb, payment_ids):
    found = 0
    for i in range(0, len(payment_ids), 100):
        chunk = payment_ids[i:i + 100]
        resp = ddb.batch_get_item(
            RequestItems={
                PAYMENTS_TABLE: {
                    "Keys": [{"payment_id": {"S": p}} for p in chunk]
                }
            }
        )
        found += len(resp.get("Responses", {}).get(PAYMENTS_TABLE, []))
    return found


def _rollups_match_expected(ddb, expected):
    for mer, exp in expected.items():
        resp = ddb.get_item(
            TableName=ROLLUPS_TABLE, Key={"merchant_id": {"S": mer}}
        )
        item = resp.get("Item")
        if item is None:
            return False
        pc = item.get("payment_count", {}).get("N")
        tc = item.get("total_cents", {}).get("N")
        if pc is None or tc is None:
            return False
        try:
            if int(Decimal(pc)) != exp["count"]:
                return False
            if int(Decimal(tc)) != exp["total_cents"]:
                return False
        except Exception:
            return False
    return True


@pytest.fixture(scope="session")
def pipeline_state(aws_clients):
    """Send the controlled payment stream, wait for the pipeline to converge."""
    sqs = aws_clients["sqs"]
    ddb = aws_clients["ddb"]

    queue_url = sqs.get_queue_url(QueueName=QUEUE_NAME)["QueueUrl"]
    _send_batch(sqs, queue_url, ALL_PAYMENTS)

    # Wait for all payments to appear in the `payments` table.
    deadline = time.time() + 120
    payments_found = 0
    while time.time() < deadline:
        payments_found = _payments_present(ddb, ALL_PAYMENT_IDS)
        if payments_found == len(ALL_PAYMENT_IDS):
            break
        time.sleep(3)
    if payments_found != len(ALL_PAYMENT_IDS):
        pytest.fail(
            "pipeline did not persist the controlled payment stream within 120s: "
            f"found {payments_found}/{len(ALL_PAYMENT_IDS)} payment rows"
        )

    # Wait for rollups to reach the expected shape.
    deadline = time.time() + 90
    rollups_ready = False
    while time.time() < deadline:
        if _rollups_match_expected(ddb, EXPECTED):
            rollups_ready = True
            break
        time.sleep(3)
    if not rollups_ready:
        pytest.fail("merchant rollups did not converge to the expected shape within 90s")

    # Extra settle time for trailing stream writes.
    time.sleep(5)

    return {
        "queue_url": queue_url,
        "expected": EXPECTED,
        "all_payment_ids": ALL_PAYMENT_IDS,
    }


# ---------------------------------------------------------------------------
# Tests — ordered most-likely-to-expose-failure first
# ---------------------------------------------------------------------------


def test_payment_amount_exact_fidelity(aws_clients, pipeline_state):
    """The monetary amount must be stored with the exact fidelity the
    processor transmitted. Naive float round-trips (e.g. `Decimal(float(x))`
    or storing `str(float(x))`) corrupt the value for common amounts.
    """
    ddb = aws_clients["ddb"]
    samples = [
        (_pid("a01"), "12.34"),
        (_pid("a03"), "0.29"),
        (_pid("d01"), "4.35"),
        (_pid("g01"), "99.99"),
        (_pid("e01"), "0.01"),
        (_pid("z02"), "250.50"),
        (_pid("k01"), "0.125"),
        (_pid("i03"), "0.155"),
    ]
    for payment_id, expected_amt in samples:
        resp = ddb.get_item(
            TableName=PAYMENTS_TABLE, Key={"payment_id": {"S": payment_id}}
        )
        item = resp.get("Item")
        assert item is not None, f"payment {payment_id} missing from {PAYMENTS_TABLE}"
        amt_attr = item.get("amount")
        assert amt_attr is not None, f"payment {payment_id} missing amount attribute"
        stored = amt_attr.get("N") or amt_attr.get("S")
        assert stored is not None, (
            f"payment {payment_id} amount has unexpected type: {amt_attr!r}"
        )
        assert Decimal(stored) == Decimal(expected_amt), (
            f"payment {payment_id} amount fidelity lost: stored={stored!r}, "
            f"expected exactly {expected_amt!r}"
        )


def test_total_cents_floor_semantics_with_float_trap(aws_clients, pipeline_state):
    """Exercise the arithmetic pitfalls encoded in the correctness spec.

    Each of these merchants has an expected `total_cents` that common
    shortcut implementations get wrong:

      * alpha:   12.34 + 5.67 + 0.29    -> 1830  (naive float 0.29 -> 28c)
      * beta:    10 * 0.10              -> 100   (naive float sum -> 99c)
      * delta:   3 * 4.35               -> 1305  (naive float 4.35*100 -> 434)
      * kappa:   3 * 0.125              -> 37    (sum-then-floor != floor-then-sum)
      * theta:   8 * 0.125              -> 100   (per-payment cent floors sum to 96)
      * iota:    2.50+2.50+0.155+0.155  -> 531   (per-payment floors sum to 530)

    The kappa/theta/iota cases specifically catch pipelines that maintain
    `total_cents` as a running integer-cents counter updated by ADD of each
    payment's floor(100 * amount). The spec is explicit: total_cents is the
    floor of the sum, not the sum of the per-payment floors.
    """
    ddb = aws_clients["ddb"]
    traps = {
        _mid("alpha"): 1830,
        _mid("beta"): 100,
        _mid("delta"): 1305,
        _mid("kappa"): 37,
        _mid("theta"): 100,
        _mid("iota"): 531,
    }
    for mer, expected_cents in traps.items():
        resp = ddb.get_item(
            TableName=ROLLUPS_TABLE, Key={"merchant_id": {"S": mer}}
        )
        item = resp.get("Item")
        assert item is not None, f"rollup row missing for {mer}"
        tc_attr = item.get("total_cents")
        assert tc_attr is not None, f"{mer} rollup missing total_cents"
        assert "N" in tc_attr, (
            f"{mer} total_cents must be a DynamoDB Number, got {tc_attr!r}"
        )
        actual = int(Decimal(tc_attr["N"]))
        assert actual == expected_cents, (
            f"{mer}: total_cents={actual}, expected {expected_cents}. "
            f"The correctness spec defines total_cents as "
            f"floor(100 * sum(p.amount)); small deltas here indicate an "
            f"aggregator that deviates from that formula."
        )


def test_total_cents_is_integer_no_decimal_component(aws_clients, pipeline_state):
    """`total_cents` must be an integer. Accounting rejects fractional
    cents on type grounds — a value like "1830.0" is a reject.
    """
    ddb = aws_clients["ddb"]
    for mer in EXPECTED:
        resp = ddb.get_item(
            TableName=ROLLUPS_TABLE, Key={"merchant_id": {"S": mer}}
        )
        item = resp.get("Item")
        assert item is not None, f"rollup missing for {mer}"
        tc = item.get("total_cents", {})
        assert "N" in tc, (
            f"{mer} total_cents is not a DynamoDB Number: {tc!r}"
        )
        raw = tc["N"]
        assert "." not in raw and "e" not in raw.lower(), (
            f"{mer} total_cents has a decimal/exponent component: {raw!r}"
        )
        assert Decimal(raw) == Decimal(raw).to_integral_value(), (
            f"{mer} total_cents is not an integer value: {raw!r}"
        )


def test_reconciliation_holds_globally(aws_clients, pipeline_state):
    """For *every* merchant present in `merchant_rollups` (not just ours),
    the rollup values must equal the values derived from `payments` — zero
    drift, zero rounding, zero off-by-one.

    This is the universal correctness invariant from the instruction.
    """
    ddb = aws_clients["ddb"]
    paginator = ddb.get_paginator("scan")

    rollup_items = []
    for page in paginator.paginate(TableName=ROLLUPS_TABLE):
        rollup_items.extend(page.get("Items", []))
    assert rollup_items, (
        "merchant_rollups is empty — the pipeline produced no rollups at all"
    )

    payments_by_merchant = {}
    for page in paginator.paginate(TableName=PAYMENTS_TABLE):
        for it in page.get("Items", []):
            mer = it.get("merchant_id", {}).get("S")
            amt_attr = it.get("amount", {})
            amt_str = amt_attr.get("N") or amt_attr.get("S")
            if mer is None or amt_str is None:
                continue
            payments_by_merchant.setdefault(mer, []).append(Decimal(amt_str))

    mismatches = []
    for item in rollup_items:
        mer = item.get("merchant_id", {}).get("S")
        pc_attr = item.get("payment_count", {})
        tc_attr = item.get("total_cents", {})
        if "N" not in pc_attr or "N" not in tc_attr:
            mismatches.append(
                f"{mer}: non-Number attributes (pc={pc_attr}, tc={tc_attr})"
            )
            continue
        rollup_count = int(Decimal(pc_attr["N"]))
        rollup_cents = int(Decimal(tc_attr["N"]))

        amts = payments_by_merchant.get(mer, [])
        expected_count = len(amts)
        expected_cents = int(
            (sum(amts, Decimal(0)) * Decimal(100))
            .to_integral_value(rounding=ROUND_FLOOR)
        )
        if rollup_count != expected_count or rollup_cents != expected_cents:
            mismatches.append(
                f"{mer}: rollup=(count={rollup_count}, cents={rollup_cents}) "
                f"vs derived=(count={expected_count}, cents={expected_cents})"
            )

    assert not mismatches, (
        "Reconciliation drift detected:\n  " + "\n  ".join(mismatches)
    )


def test_pipeline_converges_for_late_arriving_payment(aws_clients, pipeline_state):
    """The pipeline must process new payments automatically, with no manual
    trigger and no batch job. Send a fresh payment after initial convergence
    and verify both tables update on their own.
    """
    sqs = aws_clients["sqs"]
    ddb = aws_clients["ddb"]
    queue_url = pipeline_state["queue_url"]

    new_merchant = _mid("late")
    new_payment = _pid("late01")
    sqs.send_message(
        QueueUrl=queue_url,
        MessageBody=_message_body(new_payment, new_merchant, "7.77"),
    )

    deadline = time.time() + 120
    last_payment_item = None
    last_rollup_item = None
    while time.time() < deadline:
        p = ddb.get_item(
            TableName=PAYMENTS_TABLE, Key={"payment_id": {"S": new_payment}}
        )
        r = ddb.get_item(
            TableName=ROLLUPS_TABLE, Key={"merchant_id": {"S": new_merchant}}
        )
        last_payment_item = p.get("Item")
        last_rollup_item = r.get("Item")
        if (
            last_payment_item is not None
            and last_rollup_item is not None
            and last_rollup_item.get("payment_count", {}).get("N") == "1"
            and Decimal(last_rollup_item.get("total_cents", {}).get("N", "-1"))
            == Decimal(777)
        ):
            return
        time.sleep(3)

    pytest.fail(
        "pipeline did not converge for a late-arriving payment within 120s: "
        f"payment_row={last_payment_item!r}, rollup_row={last_rollup_item!r}"
    )


def test_total_cents_correctness_per_test_merchant(aws_clients, pipeline_state):
    """Every merchant we injected must have the exact expected total_cents."""
    ddb = aws_clients["ddb"]
    for mer, exp in EXPECTED.items():
        resp = ddb.get_item(
            TableName=ROLLUPS_TABLE, Key={"merchant_id": {"S": mer}}
        )
        item = resp.get("Item")
        assert item is not None, f"{mer} missing rollup row"
        tc = item.get("total_cents", {}).get("N")
        assert tc is not None, f"{mer} missing total_cents Number attribute"
        assert int(Decimal(tc)) == exp["total_cents"], (
            f"{mer}: total_cents={tc}, expected {exp['total_cents']}"
        )


def test_payment_count_correctness_per_test_merchant(aws_clients, pipeline_state):
    """Every merchant we injected must have the exact expected payment_count."""
    ddb = aws_clients["ddb"]
    for mer, exp in EXPECTED.items():
        resp = ddb.get_item(
            TableName=ROLLUPS_TABLE, Key={"merchant_id": {"S": mer}}
        )
        item = resp.get("Item")
        assert item is not None, f"{mer} missing rollup row"
        pc = item.get("payment_count", {}).get("N")
        assert pc is not None, f"{mer} missing payment_count Number attribute"
        assert int(Decimal(pc)) == exp["count"], (
            f"{mer}: payment_count={pc}, expected {exp['count']}"
        )


def test_rollup_attributes_are_dynamodb_numbers(aws_clients, pipeline_state):
    """`payment_count` and `total_cents` must be stored as DynamoDB Number
    attributes (type `N`) — not strings or any other type.
    """
    ddb = aws_clients["ddb"]
    for mer in EXPECTED:
        resp = ddb.get_item(
            TableName=ROLLUPS_TABLE, Key={"merchant_id": {"S": mer}}
        )
        item = resp.get("Item")
        assert item is not None, f"{mer} missing rollup row"
        pc = item.get("payment_count", {})
        tc = item.get("total_cents", {})
        assert list(pc.keys()) == ["N"], (
            f"{mer}: payment_count is not DynamoDB Number type: {pc!r}"
        )
        assert list(tc.keys()) == ["N"], (
            f"{mer}: total_cents is not DynamoDB Number type: {tc!r}"
        )


def test_no_phantom_merchants_in_rollups(aws_clients, pipeline_state):
    """A rollup row must only exist for merchants that actually have
    payments in the `payments` table.
    """
    ddb = aws_clients["ddb"]
    paginator = ddb.get_paginator("scan")

    rollup_merchants = set()
    for page in paginator.paginate(TableName=ROLLUPS_TABLE):
        for it in page.get("Items", []):
            mid = it.get("merchant_id", {}).get("S")
            if mid:
                rollup_merchants.add(mid)

    payment_merchants = set()
    for page in paginator.paginate(TableName=PAYMENTS_TABLE):
        for it in page.get("Items", []):
            mid = it.get("merchant_id", {}).get("S")
            if mid:
                payment_merchants.add(mid)

    phantoms = rollup_merchants - payment_merchants
    assert not phantoms, (
        f"merchant_rollups contains phantom merchants (no payments): "
        f"{sorted(phantoms)}"
    )


def test_every_paid_merchant_has_rollup_row(aws_clients, pipeline_state):
    """Every merchant_id that appears in `payments` must have a rollup row."""
    ddb = aws_clients["ddb"]
    paginator = ddb.get_paginator("scan")

    payment_merchants = set()
    for page in paginator.paginate(TableName=PAYMENTS_TABLE):
        for it in page.get("Items", []):
            mid = it.get("merchant_id", {}).get("S")
            if mid:
                payment_merchants.add(mid)

    missing = []
    for mer in sorted(payment_merchants):
        resp = ddb.get_item(
            TableName=ROLLUPS_TABLE, Key={"merchant_id": {"S": mer}}
        )
        if "Item" not in resp:
            missing.append(mer)
    assert not missing, (
        f"merchants have payments but no rollup row: {missing}"
    )


def test_payment_row_preserves_processor_fields(aws_clients, pipeline_state):
    """The ledger row must contain the fields the processor sent."""
    ddb = aws_clients["ddb"]
    pid = _pid("a01")
    expected_merchant = _mid("alpha")
    resp = ddb.get_item(
        TableName=PAYMENTS_TABLE, Key={"payment_id": {"S": pid}}
    )
    item = resp.get("Item")
    assert item is not None, f"payment {pid} missing from ledger"

    assert item.get("merchant_id", {}).get("S") == expected_merchant, (
        f"merchant_id not preserved on {pid}: {item.get('merchant_id')!r}"
    )
    assert item.get("currency", {}).get("S") == "USD", (
        f"currency not preserved on {pid}: {item.get('currency')!r}"
    )
    assert item.get("created_at", {}).get("S") == "2026-04-16T14:02:11.412Z", (
        f"created_at not preserved on {pid}: {item.get('created_at')!r}"
    )
    assert "amount" in item, f"amount attribute missing on {pid}"
    assert "payer" in item, f"payer field dropped on {pid}"


def test_all_injected_payments_landed_in_ledger(aws_clients, pipeline_state):
    """All payments sent to `payments-inbound` must appear in `payments`."""
    ddb = aws_clients["ddb"]
    missing = []
    for pid in pipeline_state["all_payment_ids"]:
        resp = ddb.get_item(
            TableName=PAYMENTS_TABLE, Key={"payment_id": {"S": pid}}
        )
        if "Item" not in resp:
            missing.append(pid)
    assert not missing, (
        f"{len(missing)} payments failed to land in the ledger. "
        f"First few: {missing[:5]}"
    )


def test_injected_merchants_all_have_rollup_rows(aws_clients, pipeline_state):
    """Every test merchant has a rollup row."""
    ddb = aws_clients["ddb"]
    missing = []
    for mer in EXPECTED:
        resp = ddb.get_item(
            TableName=ROLLUPS_TABLE, Key={"merchant_id": {"S": mer}}
        )
        if "Item" not in resp:
            missing.append(mer)
    assert not missing, f"test merchants with no rollup row: {missing}"


def test_required_resources_exist(aws_clients):
    """Baseline resource existence: the queue and both tables are present."""
    sqs = aws_clients["sqs"]
    ddb = aws_clients["ddb"]

    sqs.get_queue_url(QueueName=QUEUE_NAME)  # raises if missing

    p = ddb.describe_table(TableName=PAYMENTS_TABLE)["Table"]
    assert p["TableStatus"] == "ACTIVE"
    key_schema = {k["AttributeName"]: k["KeyType"] for k in p["KeySchema"]}
    assert key_schema == {"payment_id": "HASH"}, (
        f"payments table key schema changed: {key_schema}"
    )

    r = ddb.describe_table(TableName=ROLLUPS_TABLE)["Table"]
    assert r["TableStatus"] == "ACTIVE"
    key_schema = {k["AttributeName"]: k["KeyType"] for k in r["KeySchema"]}
    assert key_schema == {"merchant_id": "HASH"}, (
        f"merchant_rollups table key schema changed: {key_schema}"
    )


def test_scoped_builder_and_network_isolation(aws_clients):
    sts = boto3.client("sts", endpoint_url=ENDPOINT, region_name=REGION)
    arn = sts.get_caller_identity()["Arn"]
    assert arn.endswith(":user/ProvisioningOperator"), arn

    static_sts = boto3.client(
        "sts",
        endpoint_url=ENDPOINT,
        region_name=REGION,
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    with pytest.raises(Exception):
        static_sts.get_caller_identity()

    with pytest.raises(socket.gaierror):
        socket.getaddrinfo("localstack-backend", 4566)

    with pytest.raises(Exception):
        aws_clients["ddb"].delete_item(
            TableName=PAYMENTS_TABLE,
            Key={"payment_id": {"S": "iam-isolation-probe"}},
        )


def test_ingest_workload_uses_lambda_role():
    lam = boto3.client("lambda", endpoint_url=ENDPOINT, region_name=REGION)
    iam = boto3.client("iam", endpoint_url=ENDPOINT, region_name=REGION)
    mappings = lam.list_event_source_mappings().get("EventSourceMappings", [])
    functions = {
        mapping["FunctionArn"].rsplit(":", 1)[-1]
        for mapping in mappings
        if mapping.get("EventSourceArn", "").endswith(
            ("/payments-inbound", ":payments-inbound")
        )
    }
    assert len(functions) == 1, (
        "expected exactly one Lambda consumer of payments-inbound, "
        f"got {sorted(functions)}"
    )
    config = lam.get_function_configuration(FunctionName=functions.pop())
    role_name = config["Role"].rsplit("/", 1)[-1]
    trust = iam.get_role(RoleName=role_name)["Role"]["AssumeRolePolicyDocument"]
    principals = [
        statement.get("Principal", {}).get("Service")
        for statement in trust.get("Statement", [])
    ]
    assert "lambda.amazonaws.com" in principals
    runtime_env = config.get("Environment", {}).get("Variables", {})
    assert not {
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
    }.intersection(runtime_env)
