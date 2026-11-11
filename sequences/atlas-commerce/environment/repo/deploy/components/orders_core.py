"""Orders core: orders.fifo and its dead-letter queue, the orders table and the orders-consumer Lambda."""

import json

from support import (BASIC_EXECUTION, FUNCTION_ENDPOINT, LAMBDA_TRUST, REPO, ensure_function, ensure_queue,
                     ensure_role, ensure_sqs_mapping, ensure_table, log, package_dir)

ORDER = 10

QUEUE = "orders.fifo"
DEAD_LETTER_QUEUE = "orders-dlq.fifo"
TABLE = "orders"
FUNCTION = "orders-consumer"
ROLE = "orders-consumer-role"
SOURCE = REPO / "services" / "orders_consumer"
# Inline policy for the consumer's role, applied when the file exists (see iam/README.md).
ROLE_POLICY_FILE = REPO / "iam" / "orders-consumer-role.json"

BATCH_SIZE = 5
FUNCTION_RESPONSE_TYPES: list[str] = []


def deploy() -> None:
    _, dlq_arn, _ = ensure_queue(DEAD_LETTER_QUEUE, {
        "FifoQueue": "true",
        "ContentBasedDeduplication": "false",
        "MessageRetentionPeriod": "345600",
    })
    _, queue_arn, _ = ensure_queue(QUEUE, {
        "FifoQueue": "true",
        "ContentBasedDeduplication": "false",
        "VisibilityTimeout": "180",
        "MessageRetentionPeriod": "345600",
        "RedrivePolicy": json.dumps({"deadLetterTargetArn": dlq_arn, "maxReceiveCount": "3"}),
    })
    ensure_table(TABLE, "order_id")

    inline = {}
    if ROLE_POLICY_FILE.is_file():
        inline["OrdersConsumerPolicy"] = json.dumps(json.loads(ROLE_POLICY_FILE.read_text()))
    role_arn = ensure_role(ROLE, LAMBDA_TRUST, managed=[BASIC_EXECUTION], inline=inline)

    ensure_function(
        FUNCTION,
        role_arn=role_arn,
        runtime="python3.11",
        handler="handler.lambda_handler",
        code=package_dir(SOURCE),
        environment={"AWS_ENDPOINT_URL": FUNCTION_ENDPOINT, "TABLE_NAME": TABLE},
        timeout=30,
        memory=512,
    )
    ensure_sqs_mapping(FUNCTION, queue_arn, batch_size=BATCH_SIZE, response_types=FUNCTION_RESPONSE_TYPES)
    log(f"orders core ready: {QUEUE} -> {FUNCTION} -> {TABLE}")
