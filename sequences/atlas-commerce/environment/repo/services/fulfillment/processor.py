import json
import boto3
import os
import logging
import time

logger = logging.getLogger()
logger.setLevel(os.environ.get('LOG_LEVEL', 'INFO'))

TABLE_NAME = os.environ.get('FULFILLMENT_TABLE', 'FulfillmentTable')
SERVICE_NAME = 'processor-service'
MAX_RETRIES = 3
RETRY_BASE_DELAY = 0.5


def write_order(dynamo_client, order):
    """Persist an order to the fulfillment table with processing counter."""
    order_id = order.get('order_id')
    if not order_id:
        logger.warning(f"[{SERVICE_NAME}] Skipping order without order_id")
        return False

    logger.info(f"[{SERVICE_NAME}] Processing order {order_id}")

    for attempt in range(MAX_RETRIES):
        try:
            dynamo_client.update_item(
                TableName=TABLE_NAME,
                Key={'order_id': {'S': str(order_id)}},
                UpdateExpression='ADD processing_count :inc',
                ExpressionAttributeValues={':inc': {'N': '1'}}
            )
            logger.info(f"[{SERVICE_NAME}] Order {order_id} persisted to {TABLE_NAME}")
            return True
        except Exception as e:
            delay = RETRY_BASE_DELAY * (attempt + 1)
            logger.warning(
                f"[{SERVICE_NAME}] Attempt {attempt + 1}/{MAX_RETRIES} "
                f"failed for {order_id}: {e} — retrying in {delay}s"
            )
            if attempt < MAX_RETRIES - 1:
                time.sleep(delay)

    logger.error(f"[{SERVICE_NAME}] Exhausted retries for order {order_id}")
    return False


def handler(event, context):
    logger.info(
        f"[{SERVICE_NAME}] Received batch of "
        f"{len(event.get('Records', []))} messages"
    )

    ep = os.environ["AWS_ENDPOINT_URL"]
    dynamo = boto3.client('dynamodb', endpoint_url=ep)

    success_count = 0
    failure_count = 0

    for record in event.get('Records', []):
        try:
            order = json.loads(record['body'])
            if write_order(dynamo, order):
                success_count += 1
            else:
                failure_count += 1
        except json.JSONDecodeError as jde:
            logger.error(f"[{SERVICE_NAME}] Bad JSON in message body: {jde}")
            failure_count += 1
        except Exception as exc:
            logger.error(f"[{SERVICE_NAME}] Unexpected error: {exc}", exc_info=True)
            failure_count += 1

    logger.info(
        f"[{SERVICE_NAME}] Batch complete — "
        f"{success_count} succeeded, {failure_count} failed"
    )

    if failure_count > 0:
        raise Exception(f"[{SERVICE_NAME}] {failure_count} orders failed processing")

    return {"processed": success_count, "failed": failure_count}
