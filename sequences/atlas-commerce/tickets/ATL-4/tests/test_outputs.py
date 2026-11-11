"""ATL-4 grader: the bulk fulfilment pipeline (OrdersAPI -> ingestion -> splitter -> processor).

Ten end-to-end and configuration tests. The `api_url` fixture picks the REST API named OrdersAPI
(falling back to the first REST API listed), because the account can also hold orders-api
(ATL-1); test_named_pipeline_stages_are_live_wired requires exactly one OrdersAPI wired to
IngestionFunction. The repository-parity check is in test_atl4_repo.py.
"""

import urllib.request
import json
import time
import os
import boto3
import pytest
from botocore.config import Config
from botocore.exceptions import ClientError
from boto3.dynamodb.types import TypeDeserializer


ENDPOINT = os.environ["AWS_ENDPOINT_URL"]
REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")


def _account_id(default="000000000000"):
    """Live account id; the ARNs below must not be welded to one deployment."""
    try:
        return boto3.client(
            "sts", endpoint_url=ENDPOINT, region_name=REGION
        ).get_caller_identity()["Account"]
    except Exception:
        return default


ACCOUNT_ID = _account_id()
def _client(service, credentials=None):
    kwargs = {}
    if credentials:
        kwargs = {
            "aws_access_key_id": credentials["AccessKeyId"],
            "aws_secret_access_key": credentials["SecretAccessKey"],
            "aws_session_token": credentials["SessionToken"],
        }
    return boto3.client(
        service,
        endpoint_url=ENDPOINT,
        region_name=REGION,
        config=Config(retries={"max_attempts": 1}),
        **kwargs,
    )


def _expect_denied(operation, label):
    try:
        operation()
    except ClientError as exc:
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        code = exc.response.get("Error", {}).get("Code", "")
        assert status == 403 or code in ("AccessDenied", "AccessDeniedException"), (
            f"{label} failed for a non-authorization reason: {exc}"
        )
        return
    raise AssertionError(f"{label} unexpectedly reached the data plane")


def _allow_actions(policy_document):
    """Return the actions granted by Allow statements in one IAM document."""
    statements = policy_document.get("Statement", [])
    if isinstance(statements, dict):
        statements = [statements]
    actions = []
    for statement in statements:
        if statement.get("Effect") != "Allow":
            continue
        value = statement.get("Action", [])
        actions.extend(value if isinstance(value, list) else [value])
    return [str(action).lower() for action in actions]


def _role_allow_actions(iam, role_name):
    """Inspect both inline and attached policies without assuming their names."""
    actions = []
    for policy_name in iam.list_role_policies(RoleName=role_name).get(
        "PolicyNames", []
    ):
        document = iam.get_role_policy(
            RoleName=role_name, PolicyName=policy_name
        )["PolicyDocument"]
        actions.extend(_allow_actions(document))

    for attached in iam.list_attached_role_policies(RoleName=role_name).get(
        "AttachedPolicies", []
    ):
        metadata = iam.get_policy(PolicyArn=attached["PolicyArn"])["Policy"]
        version = iam.get_policy_version(
            PolicyArn=attached["PolicyArn"],
            VersionId=metadata["DefaultVersionId"],
        )["PolicyVersion"]
        actions.extend(_allow_actions(version["Document"]))
    return actions


@pytest.fixture(scope="module")
def clients():
    return {
        "apigw": boto3.client("apigateway", endpoint_url=ENDPOINT),
        "dynamodb": boto3.client("dynamodb", endpoint_url=ENDPOINT),
        "sqs": boto3.client("sqs", endpoint_url=ENDPOINT),
        "s3": boto3.client("s3", endpoint_url=ENDPOINT),
    }


@pytest.fixture(scope="module")
def api_url(clients):
    time.sleep(15)  # Wait for setup.sh to finish deploying APIs
    apis = clients["apigw"].get_rest_apis().get("items", [])
    assert apis, "No REST APIs found in API Gateway"
    # atlas-commerce: the account also holds other teams' REST APIs (e.g. orders-api), so the
    # pipeline's API is taken by name, falling back to the first one listed (apis[0]).
    api_id = next((a["id"] for a in apis if a.get("name") == "OrdersAPI"), apis[0]["id"])
    return f"{ENDPOINT}/restapis/{api_id}/prod/_user_request_/orders"


@pytest.fixture(scope="module")
def run_id():
    import uuid
    return str(uuid.uuid4())[:8]


def test_credential_isolation_runtime_proofs():
    identity = _client("sts").get_caller_identity()
    assert ":user/ProvisioningOperator" in identity.get("Arn", ""), identity

    static_admin = boto3.client(
        "dynamodb",
        endpoint_url=ENDPOINT,
        region_name=REGION,
        aws_access_key_id="test",
        aws_secret_access_key="test",
        config=Config(retries={"max_attempts": 1}),
    )
    _expect_denied(
        static_admin.list_tables,
        "default LocalStack test credentials",
    )

    forged = boto3.client(
        "dynamodb",
        endpoint_url=ENDPOINT,
        region_name=REGION,
        aws_access_key_id="AKIAFORGEDTOKEN00001",
        aws_secret_access_key="forged-secret-key",
        aws_session_token="forged-session-token",
        config=Config(retries={"max_attempts": 1}),
    )
    _expect_denied(
        forged.list_tables,
        "static key carrying a forged session token",
    )

    probe_id = f"IAM-PROBE-{os.urandom(8).hex()}"
    builder_ddb = _client("dynamodb")
    update = {
        "TableName": "FulfillmentTable",
        "Key": {"order_id": {"S": probe_id}},
        "UpdateExpression": "SET #i = :i, processing_count = :n",
        "ExpressionAttributeNames": {"#i": "item"},
        "ExpressionAttributeValues": {
            ":i": {"S": "role-session"},
            ":n": {"N": "1"},
        },
    }
    _expect_denied(
        lambda: builder_ddb.update_item(**update),
        "ProvisioningOperator direct graded fulfillment write",
    )

    lmb = _client("lambda")
    for function_name in (
        "IngestionFunction",
        "SplitterFunction",
        "ProcessorFunction",
    ):
        cfg = lmb.get_function_configuration(FunctionName=function_name)
        variables = (cfg.get("Environment") or {}).get("Variables", {})
        assert "AWS_ACCESS_KEY_ID" not in variables
        assert "AWS_SECRET_ACCESS_KEY" not in variables


def test_ingestion_role_cannot_write_fulfillment_table():
    """The public ingestion stage must be separate from the DynamoDB writer."""
    lmb = _client("lambda")
    iam = _client("iam")
    ingestion_role = lmb.get_function_configuration(
        FunctionName="IngestionFunction"
    )["Role"].rsplit("/", 1)[-1]
    processor_role = lmb.get_function_configuration(
        FunctionName="ProcessorFunction"
    )["Role"].rsplit("/", 1)[-1]

    assert ingestion_role != processor_role, (
        "IngestionFunction and ProcessorFunction must use distinct workload roles"
    )
    ingestion_actions = _role_allow_actions(iam, ingestion_role)
    assert not any(
        action == "*" or action.startswith("dynamodb:")
        for action in ingestion_actions
    ), "IngestionFunction role must not grant DynamoDB access"


def test_named_pipeline_stages_are_live_wired():
    """Prove the graded path is API -> S3 -> standard SQS -> FIFO -> DDB."""
    apigw = _client("apigateway")
    lmb = _client("lambda")
    s3 = _client("s3")
    sqs = _client("sqs")
    iam = _client("iam")

    apis = [
        api for api in apigw.get_rest_apis().get("items", [])
        if api.get("name") == "OrdersAPI"
    ]
    assert len(apis) == 1, f"expected one OrdersAPI, got {apis!r}"
    api_id = apis[0]["id"]
    resources = apigw.get_resources(restApiId=api_id).get("items", [])
    orders = [resource for resource in resources if resource.get("path") == "/orders"]
    assert len(orders) == 1, f"OrdersAPI has no unique /orders resource: {resources!r}"
    integration = apigw.get_integration(
        restApiId=api_id,
        resourceId=orders[0]["id"],
        httpMethod="POST",
    )
    ingestion_arn = lmb.get_function_configuration(
        FunctionName="IngestionFunction"
    )["FunctionArn"]
    assert ingestion_arn in integration.get("uri", ""), (
        f"POST /orders is not wired to IngestionFunction: {integration!r}"
    )

    standard_url = sqs.get_queue_url(QueueName="raw-orders-queue")["QueueUrl"]
    fifo_url = sqs.get_queue_url(QueueName="fulfillment-queue.fifo")["QueueUrl"]
    standard_arn = sqs.get_queue_attributes(
        QueueUrl=standard_url, AttributeNames=["QueueArn"]
    )["Attributes"]["QueueArn"]
    fifo_arn = sqs.get_queue_attributes(
        QueueUrl=fifo_url, AttributeNames=["QueueArn"]
    )["Attributes"]["QueueArn"]

    notifications = s3.get_bucket_notification_configuration(
        Bucket="raw-orders-bucket"
    ).get("QueueConfigurations", [])
    assert any(
        item.get("QueueArn") == standard_arn
        and any(event.startswith("s3:ObjectCreated:") for event in item.get("Events", []))
        for item in notifications
    ), f"raw-orders-bucket is not wired to raw-orders-queue: {notifications!r}"

    mappings = lmb.list_event_source_mappings().get("EventSourceMappings", [])
    expected = {
        standard_arn: "SplitterFunction",
        fifo_arn: "ProcessorFunction",
    }
    for source_arn, function_name in expected.items():
        function_arn = lmb.get_function_configuration(
            FunctionName=function_name
        )["FunctionArn"]
        assert any(
            mapping.get("EventSourceArn") == source_arn
            and mapping.get("FunctionArn") == function_arn
            and mapping.get("State") in {"Enabled", "Enabling"}
            for mapping in mappings
        ), f"{source_arn} is not actively wired to {function_name}: {mappings!r}"

    splitter_role = lmb.get_function_configuration(
        FunctionName="SplitterFunction"
    )["Role"].rsplit("/", 1)[-1]
    processor_role = lmb.get_function_configuration(
        FunctionName="ProcessorFunction"
    )["Role"].rsplit("/", 1)[-1]
    splitter_actions = _role_allow_actions(iam, splitter_role)
    processor_actions = _role_allow_actions(iam, processor_role)
    assert not any(
        action == "*" or action.startswith("dynamodb:")
        for action in splitter_actions
    ), "SplitterFunction role must not bypass the FIFO processor into DynamoDB"
    assert any(
        action in {"*", "dynamodb:*", "dynamodb:putitem", "dynamodb:updateitem"}
        for action in processor_actions
    ), "ProcessorFunction role lacks the fulfillment write permission"


@pytest.fixture(scope="module")
def pipeline_result(api_url, clients, run_id):
    """Send a batch of orders, wait for end-to-end processing, then return DynamoDB state."""
    payload = [
        {
            "order_id": f"ORD-{run_id}-001",
            "item": "Laptop",
            "quantity": 2,
            "expedited": True,
            "tags": ["fragile", "gift"],
            "destination": {"warehouse": "north", "aisle": 7},
        },
        {
            "order_id": f"ORD-{run_id}-002",
            "item": "Mouse",
            "quantity": 3,
            "expedited": False,
            "tags": ["standard"],
            "destination": {"warehouse": "south", "aisle": 2},
        },
    ]

    req = urllib.request.Request(
        api_url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    response_headers = {}
    try:
        with urllib.request.urlopen(req) as response:
            status_code = response.getcode()
            body = response.read().decode()
            response_headers = dict(response.headers)
    except urllib.error.HTTPError as e:
        status_code = e.code
        body = e.read().decode()
        response_headers = dict(e.headers)

    # Wait for the pipeline to fully process
    dynamo = clients["dynamodb"]
    timeout = 60
    start = time.time()
    item_map = {}
    while time.time() - start < timeout:
        try:
            items = dynamo.scan(TableName="FulfillmentTable").get("Items", [])
            deserializer = TypeDeserializer()
            item_map = {
                item["order_id"]["S"]: {
                    key: deserializer.deserialize(value)
                    for key, value in item.items()
                }
                for item in items
            }
            if f"ORD-{run_id}-001" in item_map and f"ORD-{run_id}-002" in item_map:
                break
        except Exception:
            pass
        time.sleep(2)

    archive_payload_found = False
    for entry in clients["s3"].list_objects_v2(Bucket="raw-orders-bucket").get("Contents", []):
        try:
            archived_body = clients["s3"].get_object(
                Bucket="raw-orders-bucket", Key=entry["Key"]
            )["Body"].read()
            archived = json.loads(archived_body)
            archived_ids = {
                order.get("order_id") for order in archived.get("orders", [])
            }
            if {
                f"ORD-{run_id}-001",
                f"ORD-{run_id}-002",
            }.issubset(archived_ids):
                archive_payload_found = True
                break
        except Exception:
            continue

    return {
        "run_id": run_id,
        "status_code": status_code,
        "body": body,
        "response_headers": response_headers,
        "item_map": item_map,
        "api_url": api_url,
        "archive_payload_found": archive_payload_found,
        "payload": payload,
    }


def test_api_returns_201(pipeline_result):
    """POST /orders must return HTTP 201."""
    assert pipeline_result["status_code"] == 201, (
        f"Expected HTTP 201, got {pipeline_result['status_code']}. Body: {pipeline_result['body']}"
    )


def test_api_returns_accepted_body(pipeline_result):
    """POST /orders must return exactly {\"status\": \"accepted\"}."""
    body_json = json.loads(pipeline_result["body"])
    assert body_json == {"status": "accepted"}, (
        f"Expected {{\"status\": \"accepted\"}}, got {body_json}"
    )


def test_api_json_response(pipeline_result):
    """POST /orders response must be served as JSON."""
    headers = pipeline_result["response_headers"]
    content_type = headers.get("Content-Type", headers.get("content-type", ""))
    assert "application/json" in content_type, (
        f"Expected JSON content type, got '{content_type}'"
    )


def test_orders_processed_in_dynamodb(pipeline_result):
    """Every input field and type survives the complete queue-driven path."""
    item_map = pipeline_result["item_map"]
    run_id = pipeline_result["run_id"]
    ord1 = f"ORD-{run_id}-001"
    ord2 = f"ORD-{run_id}-002"
    
    assert ord1 in item_map, (
        f"{ord1} not found in FulfillmentTable. Pipeline did not process end-to-end."
    )
    assert ord2 in item_map, (
        f"{ord2} not found in FulfillmentTable. Pipeline did not process end-to-end."
    )
    
    assert item_map[ord1]["processing_count"] == 1, (
        f"{ord1} processing_count expected 1, got {item_map[ord1].get('processing_count')}"
    )
    assert item_map[ord2]["processing_count"] == 1, (
        f"{ord2} processing_count expected 1, got {item_map[ord2].get('processing_count')}"
    )
    
    expected = {order["order_id"]: order for order in pipeline_result["payload"]}
    for order_id in (ord1, ord2):
        actual_fields = {
            key: value
            for key, value in item_map[order_id].items()
            if key != "processing_count"
        }
        assert actual_fields == expected[order_id], (
            f"{order_id} input fields were lost or mutated: "
            f"expected={expected[order_id]!r}, actual={actual_fields!r}"
        )


def test_ingestion_archived_the_submitted_batch(pipeline_result):
    """The unique verifier batch traversed the raw S3 ingress stage."""
    assert pipeline_result["archive_payload_found"], (
        "the submitted orders were not found together in raw-orders-bucket; "
        "the API path may have bypassed the S3 ingestion stage"
    )


def test_exactly_once_deduplication(pipeline_result, clients):
    """Sending the same payload again must NOT increment processing_count."""
    api_url = pipeline_result["api_url"]
    run_id = pipeline_result["run_id"]
    ord1 = f"ORD-{run_id}-001"
    ord2 = f"ORD-{run_id}-002"
    
    payload = pipeline_result["payload"]

    req = urllib.request.Request(
        api_url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req)
    except urllib.error.HTTPError:
        pass  # We only care about downstream dedup effects

    # Wait for any potential duplicate processing
    time.sleep(15)

    dynamo = clients["dynamodb"]
    items = dynamo.scan(TableName="FulfillmentTable").get("Items", [])
    item_map = {
        item["order_id"]["S"]: int(item.get("processing_count", {}).get("N", 0)) 
        for item in items
    }

    assert item_map[ord1] == 1, (
        f"Deduplication failed! {ord1} processing_count increased to {item_map.get(ord1)}"
    )
    assert item_map[ord2] == 1, (
        f"Deduplication failed! {ord2} processing_count increased to {item_map.get(ord2)}"
    )


def test_queues_encrypted_with_dedicated_key(clients):
    """Both SQS queues must remain encrypted with the project's dedicated key."""
    sqs = clients["sqs"]

    std_q_url = sqs.get_queue_url(QueueName="raw-orders-queue")["QueueUrl"]
    fifo_q_url = sqs.get_queue_url(QueueName="fulfillment-queue.fifo")["QueueUrl"]

    std_attrs = sqs.get_queue_attributes(
        QueueUrl=std_q_url, AttributeNames=["KmsMasterKeyId"]
    )
    fifo_attrs = sqs.get_queue_attributes(
        QueueUrl=fifo_q_url, AttributeNames=["KmsMasterKeyId"]
    )

    std_key = std_attrs.get("Attributes", {}).get("KmsMasterKeyId", "")
    fifo_key = fifo_attrs.get("Attributes", {}).get("KmsMasterKeyId", "")

    assert std_key, (
        "raw-orders-queue must remain encrypted with the project's dedicated key"
    )
    assert fifo_key, (
        "fulfillment-queue.fifo must remain encrypted with the project's dedicated key"
    )
    assert std_key != "alias/aws/sqs", (
        "raw-orders-queue must use the project's dedicated key, not the default AWS-managed key"
    )
    assert fifo_key != "alias/aws/sqs", (
        "fulfillment-queue.fifo must use the project's dedicated key, not the default AWS-managed key"
    )
