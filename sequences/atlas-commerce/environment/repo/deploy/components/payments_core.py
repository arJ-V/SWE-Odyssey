"""Payments foundations: the payments-inbound queue and the payments and merchant_rollups tables."""

from support import ensure_queue, ensure_table, log

ORDER = 30

INBOUND_QUEUE = "payments-inbound"
LEDGER_TABLE = "payments"
ROLLUP_TABLE = "merchant_rollups"


def deploy() -> None:
    ensure_queue(INBOUND_QUEUE, {"VisibilityTimeout": "60", "MessageRetentionPeriod": "1209600"})
    ensure_table(LEDGER_TABLE, "payment_id")
    ensure_table(ROLLUP_TABLE, "merchant_id")
    log(f"payments foundations ready: {INBOUND_QUEUE}, {LEDGER_TABLE}, {ROLLUP_TABLE}")
