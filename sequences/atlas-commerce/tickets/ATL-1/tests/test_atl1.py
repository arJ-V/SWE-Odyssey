"""ATL-1 grader: the public orders API (orders-api, POST /orders) sending straight to orders.fifo.

- test_api_role_has_required_trust_and_scoped_permissions: the integration role trusts
  apigateway.amazonaws.com and may sqs:SendMessage on orders.fifo. The orders-consumer-role checks
  are ATL-3's grader.
- test_event_source_mapping_declares_report_batch_item_failures is ATL-2's (the consumer), and is
  not repeated here.
- Queue, table, consumer and builder-isolation checks, the integration and template checks, and
  three end-to-end tests that POST to the live dev stage.
The queue, table and consumer exist in the platform's base, so those checks pass before the
ticket; the API checks and the end-to-end tests fail until orders-api is deployed.
- New (validity verifier, 2026-11-11): test_api_role_sqs_grants_name_the_queue. No SQS grant on
  the integration role covers "Resource": "*". _role_has_grant counts such a grant as
  one on orders.fifo, so a role with sqs:SendMessage on "*" scored 1 without it (measured), although
  the ticket asks to keep the role scoped to the queue.
"""

import json
import os
import re
import time
import uuid

import boto3
import pytest
import requests
import urllib.request
from botocore.config import Config
from botocore.exceptions import ClientError

ENDPOINT = os.environ.get("AWS_ENDPOINT_URL", "http://localstack:4566")
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

MAIN_QUEUE = "orders.fifo"
DLQ_QUEUE = "orders-dlq.fifo"
LAMBDA_FUNC = "orders-consumer"
LAMBDA_ROLE = "orders-consumer-role"
DDB_TABLE = "orders"
API_NAME = "orders-api"
STAGE_NAME = "dev"


_cfg = Config(
    region_name=REGION,
    retries={"max_attempts": 3, "mode": "standard"},
    read_timeout=30,
    connect_timeout=10,
)


def _client(service, **credentials):
    return boto3.client(
        service,
        endpoint_url=ENDPOINT,
        region_name=REGION,
        config=_cfg,
        **credentials,
    )


@pytest.fixture(scope="module")
def iam():
    return _client("iam")


@pytest.fixture(scope="module")
def apigw():
    return _client("apigateway")


@pytest.fixture(scope="module")
def sqs():
    return _client("sqs")


@pytest.fixture(scope="module")
def lmb():
    return _client("lambda")


@pytest.fixture(scope="module")
def ddb():
    return _client("dynamodb")


def _normalise_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _queue_url(sqs_client, name):
    return sqs_client.get_queue_url(QueueName=name)["QueueUrl"]


def _find_api(apigw_client):
    apis = apigw_client.get_rest_apis()["items"]
    for a in apis:
        if a["name"] == API_NAME:
            return a["id"]
    return None


def _find_resource(apigw_client, api_id, path):
    resp = apigw_client.get_resources(restApiId=api_id, limit=500)
    for r in resp.get("items", []):
        if r.get("path") == path:
            return r["id"]
    return None


def _invoke_url(api_id, stage, path):
    return f"{ENDPOINT}/restapis/{api_id}/{stage}/_user_request_{path}"


def _drain_queue(sqs_client, url):
    for _ in range(5):
        msgs = sqs_client.receive_message(
            QueueUrl=url, MaxNumberOfMessages=10, WaitTimeSeconds=0
        ).get("Messages", [])
        if not msgs:
            return
        entries = [
            {"Id": str(i), "ReceiptHandle": m["ReceiptHandle"]}
            for i, m in enumerate(msgs)
        ]
        sqs_client.delete_message_batch(QueueUrl=url, Entries=entries)


def test_localstack_reachable():
    """LocalStack health check."""
    with urllib.request.urlopen(f"{ENDPOINT}/_localstack/health", timeout=5) as r:
        data = json.loads(r.read())
    services = data.get("services", {})
    for svc in ("apigateway", "sqs", "lambda", "iam", "dynamodb"):
        assert services.get(svc) in ("available", "running"), (
            f"LocalStack service {svc} not ready: {services.get(svc)}"
        )


def _expect_denied(operation, label):
    try:
        operation()
    except Exception as exc:
        message = str(exc).lower()
        assert (
            "denied" in message
            or "forbidden" in message
            or "not authorized" in message
            or "403" in message
        ), f"{label} failed for a non-authorization reason: {exc}"
        return
    raise AssertionError(f"{label} unexpectedly reached the data plane")


def test_credential_isolation_and_builder_bypass_guard(sqs):
    identity = _client("sts").get_caller_identity()
    assert identity["Arn"].endswith(":user/ProvisioningOperator"), identity

    for token, label in (
        (None, "default LocalStack static credentials"),
        ("forged-not-an-sts-session", "static credentials with forged token"),
    ):
        credentials = {
            "aws_access_key_id": "test",
            "aws_secret_access_key": "test",
        }
        if token:
            credentials["aws_session_token"] = token
        _expect_denied(
            lambda credentials=credentials: _client(
                "sts", **credentials
            ).get_caller_identity(),
            label,
        )

    _expect_denied(
        lambda: sqs.send_message(
            QueueUrl=_queue_url(sqs, MAIN_QUEUE),
            MessageBody='{"order_id":"builder-bypass"}',
            MessageGroupId="guard",
            MessageDeduplicationId=uuid.uuid4().hex,
        ),
        "ProvisioningOperator direct graded queue injection",
    )
    _expect_denied(
        lambda: _client("dynamodb").put_item(
            TableName=DDB_TABLE,
            Item={"order_id": {"S": "builder-bypass"}},
        ),
        "ProvisioningOperator direct graded DynamoDB write",
    )
    _expect_denied(
        lambda: _client("lambda").invoke(
            FunctionName=LAMBDA_FUNC,
            Payload=b'{"Records":[]}',
        ),
        "ProvisioningOperator direct consumer invocation",
    )


def test_main_fifo_queue_exists(sqs):
    """Main FIFO queue."""
    url = _queue_url(sqs, MAIN_QUEUE)
    attrs = sqs.get_queue_attributes(QueueUrl=url, AttributeNames=["All"])[
        "Attributes"
    ]
    assert attrs.get("FifoQueue") == "true", f"{MAIN_QUEUE} is not FIFO"
    assert attrs.get("ContentBasedDeduplication") == "false", (
        f"{MAIN_QUEUE} ContentBasedDeduplication should be false "
        f"(the task requires explicit MessageDeduplicationId)"
    )
    redrive = attrs.get("RedrivePolicy")
    assert redrive, f"{MAIN_QUEUE} missing RedrivePolicy"


def test_dlq_fifo_queue_exists(sqs):
    """DLQ FIFO queue."""
    url = _queue_url(sqs, DLQ_QUEUE)
    attrs = sqs.get_queue_attributes(QueueUrl=url, AttributeNames=["All"])[
        "Attributes"
    ]
    assert attrs.get("FifoQueue") == "true"


def test_ddb_table_exists(ddb):
    """Orders table active."""
    desc = ddb.describe_table(TableName=DDB_TABLE)["Table"]
    assert desc["TableStatus"] == "ACTIVE"


def test_lambda_and_esm_exist(lmb):
    """Lambda and ESM."""
    cfg = lmb.get_function(FunctionName=LAMBDA_FUNC)["Configuration"]
    assert cfg["Runtime"] == "python3.11"
    assert cfg["Handler"] == "handler.lambda_handler"
    variables = cfg.get("Environment", {}).get("Variables", {})
    assert "AWS_ACCESS_KEY_ID" not in variables
    assert "AWS_SECRET_ACCESS_KEY" not in variables
    mappings = lmb.list_event_source_mappings(FunctionName=LAMBDA_FUNC)[
        "EventSourceMappings"
    ]
    assert any(
        ":sqs:" in m.get("EventSourceArn", "") for m in mappings
    ), f"no SQS ESM on {LAMBDA_FUNC}: {mappings}"


def test_rest_api_exists_with_post_orders(apigw):
    """REST API exists."""
    api_id = _find_api(apigw)
    assert api_id, f"REST API {API_NAME} missing"
    resource_id = _find_resource(apigw, api_id, "/orders")
    assert resource_id, f"/orders resource missing on API {api_id}"
    method = apigw.get_method(
        restApiId=api_id, resourceId=resource_id, httpMethod="POST"
    )
    assert method["httpMethod"] == "POST"


def _integration(apigw_client):
    api_id = _find_api(apigw_client)
    resource_id = _find_resource(apigw_client, api_id, "/orders")
    return api_id, resource_id, apigw_client.get_integration(
        restApiId=api_id, resourceId=resource_id, httpMethod="POST"
    )


def _integration_role_name(apigw_client):
    _api, _res, integration = _integration(apigw_client)
    role_arn = integration.get("credentials", "")
    assert ":role/" in role_arn, (
        f"integration credentials is not an IAM role ARN: {role_arn!r}"
    )
    return role_arn.rsplit("/", 1)[-1]


def test_integration_uri_targets_fifo_queue(apigw):
    """Integration targets FIFO."""
    _api, _res, integ = _integration(apigw)
    uri = integ.get("uri", "")
    assert uri.endswith(f"/{MAIN_QUEUE}") or uri.endswith(
        f":path/{ACCOUNT_ID}/{MAIN_QUEUE}"
    ), (
        f"integration URI {uri!r} does not target the FIFO queue "
        f"{MAIN_QUEUE}. A FIFO queue name always ends in `.fifo`; if "
        f"the URI path ends in `orders` (without `.fifo`) the send "
        f"will silently fail."
    )
    assert integ.get("type") == "AWS", (
        f"integration type is {integ.get('type')!r}, expected AWS "
        f"(direct SQS service integration, not AWS_PROXY)."
    )


def test_integration_credentials_role_is_set(apigw, iam):
    """Integration has credentials."""
    _api, _res, integ = _integration(apigw)
    creds = integ.get("credentials")
    assert creds, (
        f"integration `credentials` is not set. Direct AWS integrations "
        f"need a role ARN for API Gateway to assume. Without it, API "
        f"Gateway uses caller credentials and SQS returns 403."
    )
    role_name = _integration_role_name(apigw)
    role_arn = iam.get_role(RoleName=role_name)["Role"]["Arn"]
    assert creds == role_arn, (
        f"integration credentials = {creds!r}, but the wired role resolves "
        f"to {role_arn!r}."
    )


def _role_policy_documents(iam_client, role_name):
    documents = [
        iam_client.get_role_policy(RoleName=role_name, PolicyName=name)[
            "PolicyDocument"
        ]
        for name in iam_client.list_role_policies(RoleName=role_name).get(
            "PolicyNames", []
        )
    ]
    for attached in iam_client.list_attached_role_policies(
        RoleName=role_name
    ).get("AttachedPolicies", []):
        try:
            policy = iam_client.get_policy(
                PolicyArn=attached["PolicyArn"]
            )["Policy"]
            documents.append(
                iam_client.get_policy_version(
                    PolicyArn=attached["PolicyArn"],
                    VersionId=policy["DefaultVersionId"],
                )["PolicyVersion"]["Document"]
            )
        except ClientError:
            continue
    return documents


def _role_has_grant(iam_client, role_name, action, resource):
    for document in _role_policy_documents(iam_client, role_name):
        statements = document.get("Statement", [])
        if isinstance(statements, dict):
            statements = [statements]
        for statement in statements:
            if statement.get("Effect") != "Allow":
                continue
            actions = _normalise_list(statement.get("Action"))
            resources = _normalise_list(statement.get("Resource"))
            action_ok = any(
                candidate == "*"
                or candidate == action
                or (
                    isinstance(candidate, str)
                    and candidate.endswith("*")
                    and action.startswith(candidate[:-1])
                )
                for candidate in actions
            )
            if action_ok and (resource in resources or "*" in resources):
                return True
    return False


def _role_trusts_service(iam_client, role_name, service):
    document = iam_client.get_role(RoleName=role_name)["Role"][
        "AssumeRolePolicyDocument"
    ]
    for statement in _normalise_list(document.get("Statement")):
        if statement.get("Effect") != "Allow":
            continue
        principal = statement.get("Principal", {})
        services = (
            _normalise_list(principal.get("Service"))
            if isinstance(principal, dict)
            else []
        )
        actions = _normalise_list(statement.get("Action"))
        if service in services and "sts:AssumeRole" in actions:
            return True
    return False


def test_api_role_has_required_trust_and_scoped_permissions(iam, sqs, apigw):
    main_arn = sqs.get_queue_attributes(
        QueueUrl=_queue_url(sqs, MAIN_QUEUE),
        AttributeNames=["QueueArn"],
    )["Attributes"]["QueueArn"]

    apigw_role = _integration_role_name(apigw)
    assert _role_trusts_service(
        iam, apigw_role, "apigateway.amazonaws.com"
    )
    assert _role_has_grant(
        iam, apigw_role, "sqs:SendMessage", main_arn
    )


def test_integration_sets_content_type_header(apigw):
    """Content-Type header mapping."""
    _api, _res, integ = _integration(apigw)
    params = integ.get("requestParameters") or {}
    header = params.get("integration.request.header.Content-Type")
    assert header, (
        f"integration has no requestParameters mapping for "
        f"integration.request.header.Content-Type. SQS SendMessage is "
        f"a form-encoded query API; without the header override, "
        f"API Gateway forwards the incoming application/json header "
        f"and SQS rejects the request. requestParameters={params!r}"
    )
    assert "x-www-form-urlencoded" in header.lower(), (
        f"Content-Type header mapping = {header!r}, expected a literal "
        f"containing 'application/x-www-form-urlencoded' (quoted in "
        f"single quotes, per AWS docs)."
    )


def _integration_template(integ):
    templates = integ.get("requestTemplates") or {}
    for ct in ("application/json", "application/x-www-form-urlencoded"):
        if ct in templates and templates[ct]:
            return templates[ct]
    for _k, v in templates.items():
        if v:
            return v
    return ""


def test_integration_request_template_uses_full_body(apigw):
    """Template URL-encodes an equivalent representation of the full body."""
    _api, _res, integ = _integration(apigw)
    tpl = _integration_template(integ)
    assert "Action=SendMessage" in tpl, (
        f"integration requestTemplate must declare Action=SendMessage. "
        f"Template seen: {tpl!r}"
    )
    full_body_patterns = (
        r"MessageBody=\$util\.urlEncode\(\s*\$input\.body\s*\)",
        r"MessageBody=\$util\.urlEncode\(\s*\$input\.json\(['\"]\$['\"]\)\s*\)",
        r"MessageBody=\$util\.urlEncode\(\s*\$util\.toJson\(\s*\$input\.path\(['\"]\$['\"]\)\s*\)\s*\)",
    )
    assert any(re.search(pattern, tpl) for pattern in full_body_patterns), (
        f"integration requestTemplate must URL-encode the full request "
        f"body as MessageBody. `$input.body`, `$input.json('$')`, and "
        f"`$util.toJson($input.path('$'))` are accepted; selecting one "
        f"field is not. Template seen: {tpl!r}"
    )


def test_integration_request_template_has_message_group_id(apigw):
    """Template has MessageGroupId."""
    _api, _res, integ = _integration(apigw)
    tpl = _integration_template(integ)
    m = re.search(r"MessageGroupId=([^&\s\"]+)", tpl)
    assert m, (
        f"integration requestTemplate is missing MessageGroupId. FIFO "
        f"queues require one per message. Template: {tpl!r}"
    )
    value = m.group(1)
    assert "$" in value or len(value) >= 1, (
        f"MessageGroupId must be derived from the request (e.g. "
        f"`$input.path('$.merchant_id')`) or be a non-empty literal. "
        f"Got {value!r}"
    )


def test_integration_request_template_has_dedup_id(apigw):
    """Template has dedup id."""
    _api, _res, integ = _integration(apigw)
    tpl = _integration_template(integ)
    m = re.search(r"MessageDeduplicationId=([^&\s\"]+)", tpl)
    assert m, (
        f"integration requestTemplate is missing "
        f"MessageDeduplicationId. FIFO with "
        f"ContentBasedDeduplication=false rejects sends without one. "
        f"Template: {tpl!r}"
    )


def test_integration_template_identifiers_are_request_derived(apigw):
    """Anti-hardcoded FIFO identifiers."""
    _api, _res, integ = _integration(apigw)
    tpl = _integration_template(integ)

    def _field_is_dynamic(name):
        match = re.search(rf"{name}=([^&\s\"]+)", tpl)
        if not match:
            return False, None
        raw = match.group(1)
        return ("$" in raw), raw

    grp_ok, grp_val = _field_is_dynamic("MessageGroupId")
    dedup_ok, dedup_val = _field_is_dynamic("MessageDeduplicationId")
    assert grp_ok, (
        f"MessageGroupId={grp_val!r} is hard-coded. It must be derived "
        f"from the request (e.g. $input.path('$.merchant_id'))."
    )
    assert dedup_ok, (
        f"MessageDeduplicationId={dedup_val!r} is hard-coded. It must "
        f"be derived from the request (e.g. $input.path('$.order_id'))."
    )


def test_end_to_end_post_reaches_ddb_no_dlq(apigw, sqs, ddb):
    """End-to-end POST."""
    api_id = _find_api(apigw)
    url = _invoke_url(api_id, STAGE_NAME, "/orders")
    main_url = _queue_url(sqs, MAIN_QUEUE)
    dlq_url = _queue_url(sqs, DLQ_QUEUE)
    _drain_queue(sqs, main_url)
    _drain_queue(sqs, dlq_url)

    order_id = f"o-e2e-{uuid.uuid4().hex[:8]}"
    merchant = f"m-e2e-{uuid.uuid4().hex[:6]}"
    body = {"order_id": order_id, "merchant_id": merchant, "amount": 199}

    resp = requests.post(url, json=body, timeout=10)
    assert resp.status_code == 200, (
        f"POST {url} returned {resp.status_code}: {resp.text!r}. "
        f"Expected 200 after fixing integration."
    )

    deadline = time.time() + 45
    row = None
    while time.time() < deadline:
        row = ddb.get_item(
            TableName=DDB_TABLE, Key={"order_id": {"S": order_id}}
        ).get("Item")
        if row and row.get("merchant_id", {}).get("S") == merchant:
            break
        time.sleep(2)
    assert row is not None, (
        f"no DDB row for order_id={order_id} within 45s of POST. "
        f"Check API Gateway delivery, Lambda logs, and the integration "
        f"template."
    )
    assert row.get("merchant_id", {}).get("S") == merchant, (
        f"row merchant_id = {row.get('merchant_id')}; expected "
        f"{merchant}. Most likely the VTL template dropped fields by "
        f"using $input.json('$.order_id') instead of $input.body."
    )
    assert row.get("amount", {}).get("N") == "199", (
        f"row amount = {row.get('amount')}; expected 199."
    )

    dlq_attrs = sqs.get_queue_attributes(
        QueueUrl=dlq_url, AttributeNames=["ApproximateNumberOfMessages"]
    )["Attributes"]
    assert int(dlq_attrs["ApproximateNumberOfMessages"]) == 0, (
        f"{DLQ_QUEUE} has messages after a successful POST."
    )


def test_end_to_end_fifo_group_ordering(apigw, sqs, ddb):
    """FIFO per-group ordering."""
    api_id = _find_api(apigw)
    url = _invoke_url(api_id, STAGE_NAME, "/orders")

    merchant = f"m-ord-{uuid.uuid4().hex[:6]}"
    order_a = f"o-ord-a-{uuid.uuid4().hex[:6]}"
    order_b = f"o-ord-b-{uuid.uuid4().hex[:6]}"

    ra = requests.post(
        url,
        json={"order_id": order_a, "merchant_id": merchant, "amount": 1},
        timeout=10,
    )
    assert ra.status_code == 200, f"POST A: {ra.status_code} {ra.text!r}"
    rb = requests.post(
        url,
        json={"order_id": order_b, "merchant_id": merchant, "amount": 2},
        timeout=10,
    )
    assert rb.status_code == 200, f"POST B: {rb.status_code} {rb.text!r}"

    deadline = time.time() + 45
    row_a = row_b = None
    while time.time() < deadline:
        if not row_a:
            row_a = ddb.get_item(
                TableName=DDB_TABLE, Key={"order_id": {"S": order_a}}
            ).get("Item")
        if not row_b:
            row_b = ddb.get_item(
                TableName=DDB_TABLE, Key={"order_id": {"S": order_b}}
            ).get("Item")
        if row_a and row_b:
            break
        time.sleep(2)
    assert row_a and row_b, (
        f"FIFO group ordering test: one or both rows missing "
        f"(A={bool(row_a)}, B={bool(row_b)}) within 45s. Group="
        f"{merchant!r}."
    )
    ts_a = int(row_a["created_at"]["N"])
    ts_b = int(row_b["created_at"]["N"])
    assert ts_a <= ts_b, (
        f"FIFO per-group ordering violated: order A created_at={ts_a} > "
        f"order B created_at={ts_b} despite being posted earlier. "
        f"Either MessageGroupId is not derived per-merchant or the "
        f"consumer is processing out of order."
    )

    dlq_url = _queue_url(sqs, DLQ_QUEUE)
    dlq_attrs = sqs.get_queue_attributes(
        QueueUrl=dlq_url, AttributeNames=["ApproximateNumberOfMessages"]
    )["Attributes"]
    assert int(dlq_attrs["ApproximateNumberOfMessages"]) == 0, (
        f"{DLQ_QUEUE} has messages after two successful ordered POSTs."
    )


def test_end_to_end_duplicate_post_is_idempotent(apigw, sqs, ddb):
    """Duplicate POST idempotent."""
    api_id = _find_api(apigw)
    url = _invoke_url(api_id, STAGE_NAME, "/orders")

    order_id = f"o-dup-{uuid.uuid4().hex[:8]}"
    merchant = f"m-dup-{uuid.uuid4().hex[:6]}"
    body = {"order_id": order_id, "merchant_id": merchant, "amount": 11}

    r1 = requests.post(url, json=body, timeout=10)
    assert r1.status_code == 200, f"first POST: {r1.status_code} {r1.text!r}"

    deadline = time.time() + 30
    while time.time() < deadline:
        row = ddb.get_item(
            TableName=DDB_TABLE, Key={"order_id": {"S": order_id}}
        ).get("Item")
        if row:
            break
        time.sleep(2)
    assert row, f"first row never materialized for {order_id}"
    first_created_at = row["created_at"]["N"]

    time.sleep(2)
    r2 = requests.post(url, json=body, timeout=10)
    assert r2.status_code == 200, f"second POST: {r2.status_code} {r2.text!r}"

    time.sleep(8)

    row2 = ddb.get_item(
        TableName=DDB_TABLE, Key={"order_id": {"S": order_id}}
    ).get("Item")
    assert row2, "row vanished between POSTs"
    assert row2["created_at"]["N"] == first_created_at, (
        f"duplicate POST overwrote the first row. Idempotency via "
        f"ConditionExpression is not in place."
    )

    dlq_url = _queue_url(sqs, DLQ_QUEUE)
    dlq_attrs = sqs.get_queue_attributes(
        QueueUrl=dlq_url, AttributeNames=["ApproximateNumberOfMessages"]
    )["Attributes"]
    assert int(dlq_attrs["ApproximateNumberOfMessages"]) == 0, (
        f"{DLQ_QUEUE} has messages after a duplicate POST. A correct "
        f"handler catches ConditionalCheckFailedException and treats "
        f"the duplicate as success."
    )


def test_api_role_sqs_grants_name_the_queue(apigw, iam):
    """New (atlas verifier): the integration role's SQS grants name the queue, never every resource."""
    apigw_role = _integration_role_name(apigw)
    broad = []
    for document in _role_policy_documents(iam, apigw_role):
        statements = document.get("Statement", [])
        if isinstance(statements, dict):
            statements = [statements]
        for statement in statements:
            if statement.get("Effect") != "Allow":
                continue
            actions = [a.lower() for a in _normalise_list(statement.get("Action")) if isinstance(a, str)]
            resources = _normalise_list(statement.get("Resource"))
            if "*" in resources and any(a == "*" or a.startswith("sqs:") for a in actions):
                broad.append(statement)
    assert not broad, (
        f"{apigw_role} grants SQS actions on every resource (\"Resource\": \"*\"), inline or "
        f"through an attached policy; scope them to the orders.fifo queue ARN: {broad!r}"
    )
