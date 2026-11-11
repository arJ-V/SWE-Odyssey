"""ATL-3 grader: least privilege for orders-consumer-role, shipped from the repository.

- test_consumer_role_has_required_trust_and_scoped_permissions: the role trusts lambda.amazonaws.com; it is granted sqs:ReceiveMessage, sqs:DeleteMessage and
  sqs:GetQueueAttributes on orders.fifo and dynamodb:PutItem on the orders table; it is not
  granted sqs:ChangeMessageVisibility on orders.fifo (also not through a wildcard). The API
  Gateway role half is ATL-1's grader.
- test_lambda_and_esm_exist: the consumer keeps its configuration and mapping.
- Some of the fixtures and helpers above the tests are unused here.
- test_consumer_role_policy_matches_repository: The role's inline policy is the document in
  the repository's iam/orders-consumer-role.json (read as data), which is how this repository
  ships role policies (deploy/components/orders_core.py applies it), so a policy put on the role
  by hand and not committed is flagged.
- Added by the validity verifier (2026-11-11): test_consumer_role_queue_and_table_grants_name_their_resources.
  No SQS or DynamoDB grant on the role (inline or attached) covers "Resource": "*", because
  _role_has_grant counts such a grant as one on orders.fifo (an attached
  AWSLambdaSQSQueueExecutionRole plus a table-only file scored 1 without it).
The wildcard-role guard runs after these (run.sh).
"""

import json
import os
import re
import sys
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


def test_consumer_role_has_required_trust_and_scoped_permissions(iam, sqs):
    main_arn = sqs.get_queue_attributes(
        QueueUrl=_queue_url(sqs, MAIN_QUEUE),
        AttributeNames=["QueueArn"],
    )["Attributes"]["QueueArn"]
    table_arn = (
        f"arn:aws:dynamodb:{REGION}:{ACCOUNT_ID}:table/{DDB_TABLE}"
    )

    assert _role_trusts_service(iam, LAMBDA_ROLE, "lambda.amazonaws.com")
    for action in (
        "sqs:ReceiveMessage",
        "sqs:DeleteMessage",
        "sqs:GetQueueAttributes",
    ):
        assert _role_has_grant(iam, LAMBDA_ROLE, action, main_arn), (
            f"{LAMBDA_ROLE} lacks {action} on {main_arn}"
        )
    assert not _role_has_grant(
        iam, LAMBDA_ROLE, "sqs:ChangeMessageVisibility", main_arn
    ), f"{LAMBDA_ROLE} has unneeded sqs:ChangeMessageVisibility on {main_arn}"
    assert _role_has_grant(
        iam, LAMBDA_ROLE, "dynamodb:PutItem", table_arn
    ), f"{LAMBDA_ROLE} lacks dynamodb:PutItem on {table_arn}"


def test_consumer_role_policy_matches_repository(iam):
    """The role's inline policy is exactly the repository's iam/orders-consumer-role.json."""
    sys.path.insert(0, os.environ["ATLAS_LIB"])
    import atlas_grader as ag  # trusted grader library shipped in /tests/atlas (reads the repo as data)

    path = ag.repo() / "iam" / "orders-consumer-role.json"
    assert path.is_file(), (
        f"the repository has no iam/{LAMBDA_ROLE}.json; the role's policy is kept there and "
        f"shipped with `python3 deploy/deploy.py --component orders_core`"
    )
    try:
        expected = json.loads(path.read_text())
    except ValueError as exc:
        pytest.fail(f"iam/{LAMBDA_ROLE}.json is not valid JSON: {exc}")
    live = {
        name: iam.get_role_policy(RoleName=LAMBDA_ROLE, PolicyName=name)["PolicyDocument"]
        for name in iam.list_role_policies(RoleName=LAMBDA_ROLE).get("PolicyNames", [])
    }
    assert list(live.values()) == [expected], (
        f"{LAMBDA_ROLE} must carry exactly one inline policy, the document in "
        f"iam/{LAMBDA_ROLE}.json, as deployed by deploy/deploy.py; live inline policies: "
        f"{sorted(live)}"
    )


def test_consumer_role_queue_and_table_grants_name_their_resources(iam):
    """New (atlas verifier): no SQS or DynamoDB grant on the role covers every resource.

    _role_has_grant above counts a grant on "Resource": "*" as a
    grant on orders.fifo, so attaching the AWS-managed AWSLambdaSQSQueueExecutionRole (sqs:
    ReceiveMessage/DeleteMessage/GetQueueAttributes on "*") next to a table-only inline file
    passed the checks above (measured: reward 1). The ticket asks for the queue and the table to
    be named by their ARNs and for nothing beyond them; this check holds the role to that, for
    inline and attached policies alike. Other services' grants (the base's basic-execution logs
    policy on "*") are not this check's concern.
    """
    broad = []
    for document in _role_policy_documents(iam, LAMBDA_ROLE):
        statements = document.get("Statement", [])
        if isinstance(statements, dict):
            statements = [statements]
        for statement in statements:
            if statement.get("Effect") != "Allow":
                continue
            actions = [a.lower() for a in _normalise_list(statement.get("Action")) if isinstance(a, str)]
            resources = _normalise_list(statement.get("Resource"))
            if "*" in resources and any(
                a == "*" or a.startswith("sqs:") or a.startswith("dynamodb:") for a in actions
            ):
                broad.append(statement)
    assert not broad, (
        f"{LAMBDA_ROLE} grants queue or table actions on every resource (\"Resource\": \"*\"), "
        f"inline or through an attached policy; name orders.fifo and the orders table instead: "
        f"{broad!r}"
    )
