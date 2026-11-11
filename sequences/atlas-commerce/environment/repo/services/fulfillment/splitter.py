import json
import boto3
import uuid
import os
import logging

logger = logging.getLogger()
logger.setLevel(os.environ.get('LOG_LEVEL', 'INFO'))

FIFO_QUEUE_NAME = os.environ.get('FIFO_QUEUE', 'fulfillment-queue.fifo')
MESSAGE_GROUP = os.environ.get('MESSAGE_GROUP', 'orders')
SERVICE_NAME = 'splitter-service'
MAX_RETRIES = 3


def get_fifo_queue_url(sqs_client):
    """Resolve the FIFO queue URL by name."""
    resp = sqs_client.get_queue_url(QueueName=FIFO_QUEUE_NAME)
    return resp['QueueUrl']


def extract_s3_references(record):
    """Parse S3 event notification records from SQS message body."""
    body = json.loads(record['body'])
    if 'Records' not in body:
        logger.warning(f"[{SERVICE_NAME}] No Records key in S3 event notification body")
        return []
    refs = []
    for s3_rec in body['Records']:
        bucket = s3_rec['s3']['bucket']['name']
        key = s3_rec['s3']['object']['key']
        refs.append((bucket, key))
    return refs


def fetch_order_data(s3_client, bucket, key):
    """Download and parse order data from S3."""
    logger.info(f"[{SERVICE_NAME}] Fetching s3://{bucket}/{key}")
    obj = s3_client.get_object(Bucket=bucket, Key=key)
    raw = obj['Body'].read().decode('utf-8')
    data = json.loads(raw)
    return data


def handler(event, context):
    logger.info(f"[{SERVICE_NAME}] Received {len(event.get('Records', []))} SQS records")

    ep = os.environ["AWS_ENDPOINT_URL"]
    s3 = boto3.client('s3', endpoint_url=ep)
    sqs = boto3.client('sqs', endpoint_url=ep)

    queue_url = get_fifo_queue_url(sqs)
    processed_count = 0
    error_count = 0

    for record in event.get('Records', []):
        try:
            s3_refs = extract_s3_references(record)
            for bucket, key in s3_refs:
                data = fetch_order_data(s3, bucket, key)

                orders = data.get('orders', [])

                for order in orders:
                    try:
                        dedup_id = str(uuid.uuid4())

                        sqs.send_message(
                            QueueUrl=queue_url,
                            MessageBody=json.dumps(json.dumps(order)),
                            MessageGroupId=MESSAGE_GROUP,
                            MessageDeduplicationId=dedup_id
                        )
                        processed_count += 1
                        logger.debug(f"[{SERVICE_NAME}] Sent order to FIFO queue")
                    except Exception as send_err:
                        logger.error(f"[{SERVICE_NAME}] Failed to enqueue message: {send_err}")
                        error_count += 1
        except Exception as e:
            logger.error(f"[{SERVICE_NAME}] Error processing SQS record: {e}", exc_info=True)
            error_count += 1

    logger.info(f"[{SERVICE_NAME}] Completed: {processed_count} dispatched, {error_count} errors")
    return {"processed": processed_count, "errors": error_count}
