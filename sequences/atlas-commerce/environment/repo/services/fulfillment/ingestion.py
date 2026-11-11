import json
import boto3
import uuid
import time
import os
import logging
import hashlib

logger = logging.getLogger()
logger.setLevel(os.environ.get('LOG_LEVEL', 'INFO'))

BUCKET_NAME = os.environ.get('ORDER_BUCKET', 'raw-orders-bucket')
REGION = os.environ.get('AWS_DEFAULT_REGION', 'us-east-1')
SERVICE_NAME = 'ingestion-service'
VERSION = '2.1.0'


def validate_payload(orders):
    """Validate incoming order batch structure."""
    if not isinstance(orders, list):
        raise ValueError(f"Expected list of orders, got {type(orders).__name__}")
    if len(orders) == 0:
        raise ValueError("Empty order batch received")
    for idx, order in enumerate(orders):
        if not isinstance(order, dict):
            raise ValueError(f"Order at index {idx} is not an object")
    return True


def build_envelope(orders, source='api-gateway'):
    """Wrap raw orders in a metadata envelope for downstream processing."""
    return {
        "metadata": {
            "received_at": str(time.time()),
            "source": source,
            "version": VERSION,
            "service": SERVICE_NAME,
            "batch_size": len(orders),
            "region": REGION,
            "checksum": hashlib.md5(json.dumps(orders, sort_keys=True).encode()).hexdigest()
        },
        "orders": orders
    }


def handler(event, context):
    request_id = context.aws_request_id if context else str(uuid.uuid4())
    logger.info(f"[{SERVICE_NAME}] Processing request {request_id}")

    try:
        ep = os.environ["AWS_ENDPOINT_URL"]
        s3 = boto3.client('s3', endpoint_url=ep)

        body = event.get('body', '[]')
        if isinstance(body, str):
            orders = json.loads(body)
        else:
            orders = body

        validate_payload(orders)
        logger.info(f"[{SERVICE_NAME}] Validated batch of {len(orders)} orders")

        envelope = build_envelope(orders)
        object_key = f"{int(time.time())}-{uuid.uuid4()}"

        s3.put_object(
            Bucket=BUCKET_NAME,
            Key=object_key,
            Body=json.dumps(envelope),
            ContentType='application/json'
        )

        logger.info(f"[{SERVICE_NAME}] Stored batch as s3://{BUCKET_NAME}/{object_key}")

        return {
            "statusCode": 201,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"status": "accepted"})
        }
    except ValueError as ve:
        logger.warning(f"[{SERVICE_NAME}] Validation error: {ve}")
        return {
            "statusCode": 400,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": str(ve)})
        }
    except Exception as exc:
        logger.error(f"[{SERVICE_NAME}] Unexpected error: {exc}", exc_info=True)
        return {
            "statusCode": 500,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": "Internal server error"})
        }
