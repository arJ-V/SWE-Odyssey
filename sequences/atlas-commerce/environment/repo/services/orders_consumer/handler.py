"""Orders consumer.

Reads order messages from the orders.fifo queue (through the SQS event source
mapping) and stores each order in the orders table.
"""

import json
import os
import time
import boto3

_endpoint = os.environ.get("AWS_ENDPOINT_URL", "http://localstack:4566")
ddb = boto3.client("dynamodb", endpoint_url=_endpoint)

TABLE_NAME = os.environ.get("TABLE_NAME", "orders")


def _process_record(record):
    payload = json.loads(record["body"])
    order_id = payload["order_id"]
    merchant_id = payload["merchant_id"]
    amount = int(payload["amount"])

    ddb.put_item(
        TableName=TABLE_NAME,
        Item={
            "order_id": {"S": order_id},
            "merchant_id": {"S": merchant_id},
            "amount": {"N": str(amount)},
            "created_at": {"N": str(int(time.time()))},
        },
    )


def lambda_handler(event, context):
    failed = []
    for record in event.get("Records", []):
        try:
            _process_record(record)
        except Exception as e:
            print(f"[error] msgId={record.get('messageId')} {type(e).__name__}: {e}")
            failed.append({"itemIdentifier": record["messageId"]})

    return {"failed": failed}
