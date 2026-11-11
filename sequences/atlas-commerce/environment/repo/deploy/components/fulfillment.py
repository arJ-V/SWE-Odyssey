"""Bulk fulfilment: OrdersAPI -> IngestionFunction -> raw-orders-bucket -> raw-orders-queue -> SplitterFunction -> fulfillment-queue.fifo -> ProcessorFunction -> FulfillmentTable."""

import json
import time

from botocore.exceptions import ClientError

from support import (FUNCTION_ENDPOINT, LAMBDA_TRUST, REPO, account_id, client, ensure_function, ensure_queue,
                     ensure_role, ensure_sqs_mapping, ensure_table, log, package_files)

ORDER = 20

SOURCE = REPO / "services" / "fulfillment"
API_NAME = "OrdersAPI"
STAGE = "prod"
BUCKET = "raw-orders-bucket"
TABLE = "FulfillmentTable"
FIFO_QUEUE = "fulfillment-queue.fifo"
RAW_QUEUE = "raw-orders-queue"
RAW_DLQ = "raw-orders-dlq"
PIPELINE_KEY_ALIAS = "alias/order-pipeline-key"
ARCHIVE_KEY_ALIAS = "alias/order-archive-key"

# (module in services/fulfillment/, function name, execution role)
FUNCTIONS = [
    ("ingestion", "IngestionFunction", "ingestion-execution-role"),
    ("splitter", "SplitterFunction", "splitter-execution-role"),
    ("processor", "ProcessorFunction", "pipeline-execution-role"),
]

LOG_ACTIONS = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]

# Left in the dead-letter queue by the pre-launch load test.
LOAD_TEST_MESSAGES = [
    {"error": "ConnectionTimeout", "message": "Failed to connect to processing endpoint after 30s",
     "timestamp": "2024-01-15T08:30:00Z", "order_ids": ["ORD-PRELAUNCH-001", "ORD-PRELAUNCH-002"],
     "retry_count": 3, "function": "SplitterFunction"},
    {"error": "ThrottlingException", "message": "Rate exceeded for SendMessage on fulfillment-queue.fifo",
     "timestamp": "2024-01-15T08:31:00Z", "batch_id": "batch-20240115-001", "source": "load-test"},
    {"error": "KMSAccessDenied", "message": "The ciphertext refers to a customer master key that does not exist",
     "timestamp": "2024-01-15T08:32:00Z", "key_alias": "alias/order-archive-key", "queue": "fulfillment-queue.fifo"},
]


def _key(alias: str, **create_args) -> tuple[str, str]:
    """The key behind `alias`, created with `create_args` if the alias does not exist yet."""
    kms = client("kms")
    for entry in kms.list_aliases().get("Aliases", []):
        if entry.get("AliasName") == alias and entry.get("TargetKeyId"):
            meta = kms.describe_key(KeyId=entry["TargetKeyId"])["KeyMetadata"]
            return meta["KeyId"], meta["Arn"]
    meta = kms.create_key(**create_args)["KeyMetadata"]
    kms.create_alias(AliasName=alias, TargetKeyId=meta["KeyId"])
    log(f"created key {alias}")
    return meta["KeyId"], meta["Arn"]


def _pipeline_key_policy(role_arns: list[str]) -> dict:
    account = account_id()
    return {
        "Version": "2012-10-17",
        "Statement": [
            {"Sid": "RootAccess", "Effect": "Allow", "Principal": {"AWS": f"arn:aws:iam::{account}:root"},
             "Action": "kms:*", "Resource": "*"},
            {"Sid": "LambdaAccess", "Effect": "Allow", "Principal": {"AWS": role_arns},
             "Action": ["kms:Decrypt", "kms:GenerateDataKey"], "Resource": "*"},
            {"Sid": "S3Access", "Effect": "Allow", "Principal": {"Service": "s3.amazonaws.com"},
             "Action": ["kms:GenerateDataKey", "kms:Decrypt"], "Resource": "*"},
            {"Sid": "SQSAccess", "Effect": "Allow", "Principal": {"Service": "sqs.amazonaws.com"},
             "Action": ["kms:GenerateDataKey", "kms:Decrypt"], "Resource": "*"},
        ],
    }


def _role_policies(table_arn: str, fifo_arn: str, raw_arn: str, key_arn: str) -> dict[str, dict]:
    logs = {"Effect": "Allow", "Action": LOG_ACTIONS, "Resource": "*"}
    kms = {"Effect": "Allow", "Action": ["kms:Decrypt", "kms:GenerateDataKey"], "Resource": key_arn}
    return {
        # Least-privilege workload policies. Ingestion, splitting and processing use separate
        # roles so the public API path cannot write the fulfilment table directly.
        "pipeline-execution-role": {"pipeline-runtime-access": {"Version": "2012-10-17", "Statement": [
            {"Effect": "Allow", "Action": ["dynamodb:UpdateItem"], "Resource": table_arn},
            {"Effect": "Allow", "Action": ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes",
                                           "sqs:ChangeMessageVisibility"], "Resource": fifo_arn},
            kms, logs]}},
        "ingestion-execution-role": {"ingestion-runtime-access": {"Version": "2012-10-17", "Statement": [
            {"Effect": "Allow", "Action": "s3:PutObject", "Resource": f"arn:aws:s3:::{BUCKET}/*"},
            logs]}},
        "splitter-execution-role": {"splitter-runtime-access": {"Version": "2012-10-17", "Statement": [
            {"Effect": "Allow", "Action": "s3:ListBucket", "Resource": f"arn:aws:s3:::{BUCKET}"},
            {"Effect": "Allow", "Action": "s3:GetObject", "Resource": f"arn:aws:s3:::{BUCKET}/*"},
            {"Effect": "Allow", "Action": ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes",
                                           "sqs:ChangeMessageVisibility"], "Resource": raw_arn},
            {"Effect": "Allow", "Action": ["sqs:SendMessage", "sqs:GetQueueUrl"], "Resource": fifo_arn},
            kms, logs]}},
    }


def _bucket() -> None:
    s3 = client("s3")
    try:
        s3.head_bucket(Bucket=BUCKET)
    except ClientError:
        s3.create_bucket(Bucket=BUCKET)
        log(f"created bucket {BUCKET}")


def _api(ingestion_arn: str) -> str:
    apigw = client("apigateway")
    region = apigw.meta.region_name
    api_id = next((a["id"] for a in apigw.get_rest_apis(limit=500).get("items", []) if a.get("name") == API_NAME), None)
    if api_id is None:
        api_id = apigw.create_rest_api(name=API_NAME)["id"]
        log(f"created REST API {API_NAME}")
    resources = apigw.get_resources(restApiId=api_id, limit=500).get("items", [])
    root_id = next(r["id"] for r in resources if r.get("path") == "/")
    orders = next((r["id"] for r in resources if r.get("path") == "/orders"), None)
    if orders is None:
        orders = apigw.create_resource(restApiId=api_id, parentId=root_id, pathPart="orders")["id"]
    try:
        apigw.get_method(restApiId=api_id, resourceId=orders, httpMethod="POST")
    except ClientError:
        apigw.put_method(restApiId=api_id, resourceId=orders, httpMethod="POST", authorizationType="NONE")
    uri = f"arn:aws:apigateway:{region}:lambda:path/2015-03-31/functions/{ingestion_arn}/invocations"
    apigw.put_integration(restApiId=api_id, resourceId=orders, httpMethod="POST",
                          type="AWS_PROXY", integrationHttpMethod="POST", uri=uri)
    apigw.create_deployment(restApiId=api_id, stageName=STAGE)
    try:
        client("lambda").add_permission(
            FunctionName="IngestionFunction", StatementId="apigw-invoke", Action="lambda:InvokeFunction",
            Principal="apigateway.amazonaws.com",
            SourceArn=f"arn:aws:execute-api:{region}:{account_id()}:{api_id}/*/*/*")
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ResourceConflictException":
            raise
    return api_id


def deploy() -> None:
    role_arns = {role: ensure_role(role, LAMBDA_TRUST, manage_inline=False) for _, _, role in FUNCTIONS}
    time.sleep(2)

    key_id, key_arn = _key(PIPELINE_KEY_ALIAS, Description="Order pipeline encryption key",
                           Policy=json.dumps(_pipeline_key_policy([role_arns["pipeline-execution-role"],
                                                                   role_arns["ingestion-execution-role"],
                                                                   role_arns["splitter-execution-role"]])))
    _key(ARCHIVE_KEY_ALIAS, Description="Order archive encryption key - data retention compliance")

    table_arn = ensure_table(TABLE, "order_id")

    _, fifo_arn, _ = ensure_queue(FIFO_QUEUE, {"FifoQueue": "true", "ContentBasedDeduplication": "false",
                                               "KmsMasterKeyId": key_id})
    dlq_url, dlq_arn, dlq_created = ensure_queue(RAW_DLQ, {"KmsMasterKeyId": key_id})
    raw_url, raw_arn, _ = ensure_queue(RAW_QUEUE, {
        "KmsMasterKeyId": key_id,
        "RedrivePolicy": json.dumps({"deadLetterTargetArn": dlq_arn, "maxReceiveCount": "2"}),
    })
    if dlq_created:
        for message in LOAD_TEST_MESSAGES:
            client("sqs").send_message(QueueUrl=dlq_url, MessageBody=json.dumps(message))

    _bucket()
    client("sqs").set_queue_attributes(QueueUrl=raw_url, Attributes={"Policy": json.dumps({
        "Version": "2012-10-17",
        "Statement": [{"Effect": "Allow", "Principal": {"Service": "s3.amazonaws.com"},
                       "Action": "sqs:SendMessage", "Resource": raw_arn}],
    })})
    client("s3").put_bucket_notification_configuration(Bucket=BUCKET, NotificationConfiguration={
        "QueueConfigurations": [{"QueueArn": raw_arn, "Events": ["s3:ObjectCreated:*"]}],
    })

    for role, policies in _role_policies(table_arn, fifo_arn, raw_arn, key_arn).items():
        ensure_role(role, LAMBDA_TRUST, inline=policies)

    arns = {}
    for module, function, role in FUNCTIONS:
        arns[function] = ensure_function(
            function,
            role_arn=role_arns[role],
            runtime="python3.9",
            handler=f"{module}.handler",
            code=package_files(SOURCE / f"{module}.py"),
            environment={"AWS_ENDPOINT_URL": FUNCTION_ENDPOINT},
            timeout=30,
        )

    ensure_sqs_mapping("SplitterFunction", raw_arn)
    ensure_sqs_mapping("ProcessorFunction", fifo_arn)

    api_id = _api(arns["IngestionFunction"])
    log(f"fulfilment pipeline ready: {API_NAME} ({api_id}) stage {STAGE}")
