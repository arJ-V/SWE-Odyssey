"""Helpers shared by the deploy components: AWS clients, Lambda packaging, idempotent create-or-update calls.

Every ensure_* function converges the live resource to what the repository describes, so running a
component twice is safe.
"""

from __future__ import annotations

import io
import json
import os
import time
import zipfile
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

REPO = Path(__file__).resolve().parents[1]
REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
ENDPOINT = os.environ.get("AWS_ENDPOINT_URL", "http://localstack:4566")
# Lambda functions reach AWS through the same endpoint as the workstation.
FUNCTION_ENDPOINT = "http://localstack:4566"

LAMBDA_TRUST = {
    "Version": "2012-10-17",
    "Statement": [
        {"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}
    ],
}
BASIC_EXECUTION = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"

_CONFIG = Config(region_name=REGION, retries={"max_attempts": 5, "mode": "standard"},
                 connect_timeout=10, read_timeout=120)
_clients: dict[str, object] = {}
_account: list[str] = []


def client(service: str):
    if service not in _clients:
        _clients[service] = boto3.client(service, endpoint_url=ENDPOINT, config=_CONFIG)
    return _clients[service]


def account_id() -> str:
    if not _account:
        _account.append(client("sts").get_caller_identity()["Account"])
    return _account[0]


def log(message: str) -> None:
    print(f"[deploy] {message}", flush=True)


def _error_code(exc: ClientError) -> str:
    return exc.response.get("Error", {}).get("Code", "")


# --- packaging ---------------------------------------------------------------------------------

def _zip(entries: list[tuple[str, bytes]]) -> bytes:
    """A zip that depends only on file names and contents (fixed dates and modes)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(entries):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data, compresslevel=9)
    return buffer.getvalue()


def _packaged(path: Path, root: Path) -> bool:
    parts = path.relative_to(root).parts
    return (path.is_file() and not any(p.startswith(".") or p == "__pycache__" for p in parts)
            and path.suffix != ".pyc")


def package_dir(directory: Path) -> bytes:
    """Package every source file under `directory` (no dotfiles, caches or .pyc) as a Lambda zip."""
    directory = Path(directory)
    files = [p for p in directory.rglob("*") if _packaged(p, directory)]
    return _zip([(p.relative_to(directory).as_posix(), p.read_bytes()) for p in files])


def package_files(*paths: Path) -> bytes:
    """Package single module files at the zip's top level."""
    return _zip([(Path(p).name, Path(p).read_bytes()) for p in paths])


# --- IAM -----------------------------------------------------------------------------------------

def ensure_role(name: str, trust: dict, managed: list[str] = (), inline: dict[str, str | dict] | None = None,
                *, manage_inline: bool = True) -> str:
    """Create or update a role. With manage_inline, the role's inline policies become exactly `inline`."""
    iam = client("iam")
    try:
        arn = iam.get_role(RoleName=name)["Role"]["Arn"]
        iam.update_assume_role_policy(RoleName=name, PolicyDocument=json.dumps(trust))
    except ClientError as exc:
        if _error_code(exc) != "NoSuchEntity":
            raise
        arn = iam.create_role(RoleName=name, AssumeRolePolicyDocument=json.dumps(trust))["Role"]["Arn"]
        log(f"created role {name}")
    attached = {p["PolicyArn"] for p in iam.list_attached_role_policies(RoleName=name).get("AttachedPolicies", [])}
    for policy_arn in managed:
        if policy_arn not in attached:
            iam.attach_role_policy(RoleName=name, PolicyArn=policy_arn)
    inline = inline or {}
    for policy_name, document in inline.items():
        text = document if isinstance(document, str) else json.dumps(document)
        iam.put_role_policy(RoleName=name, PolicyName=policy_name, PolicyDocument=text)
    if manage_inline:
        for policy_name in iam.list_role_policies(RoleName=name).get("PolicyNames", []):
            if policy_name not in inline:
                iam.delete_role_policy(RoleName=name, PolicyName=policy_name)
                log(f"removed inline policy {policy_name} from {name}")
    return arn


# --- SQS / DynamoDB -----------------------------------------------------------------------------

def queue_arn(url: str) -> str:
    return client("sqs").get_queue_attributes(QueueUrl=url, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]


def ensure_queue(name: str, attributes: dict[str, str]) -> tuple[str, str, bool]:
    """Create the queue, or bring its attributes to `attributes`. Returns (url, arn, created)."""
    sqs = client("sqs")
    try:
        url = sqs.get_queue_url(QueueName=name)["QueueUrl"]
    except ClientError as exc:
        if _error_code(exc) not in ("AWS.SimpleQueueService.NonExistentQueue", "QueueDoesNotExist"):
            raise
        url = sqs.create_queue(QueueName=name, Attributes=attributes)["QueueUrl"]
        log(f"created queue {name}")
        return url, queue_arn(url), True
    mutable = {k: v for k, v in attributes.items() if k != "FifoQueue"}
    if mutable:
        sqs.set_queue_attributes(QueueUrl=url, Attributes=mutable)
    return url, queue_arn(url), False


def ensure_table(name: str, key: str, key_type: str = "S") -> str:
    ddb = client("dynamodb")
    try:
        arn = ddb.describe_table(TableName=name)["Table"]["TableArn"]
    except ClientError as exc:
        if _error_code(exc) != "ResourceNotFoundException":
            raise
        arn = ddb.create_table(
            TableName=name,
            KeySchema=[{"AttributeName": key, "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": key, "AttributeType": key_type}],
            BillingMode="PAY_PER_REQUEST",
        )["TableDescription"]["TableArn"]
        log(f"created table {name}")
    ddb.get_waiter("table_exists").wait(TableName=name, WaiterConfig={"Delay": 1, "MaxAttempts": 60})
    return arn


# --- Lambda --------------------------------------------------------------------------------------

def wait_function(name: str, timeout: float = 120) -> None:
    lam = client("lambda")
    deadline = time.time() + timeout
    while True:
        cfg = lam.get_function_configuration(FunctionName=name)
        if cfg.get("State") == "Active" and cfg.get("LastUpdateStatus") in (None, "Successful"):
            return
        if cfg.get("State") == "Failed" or cfg.get("LastUpdateStatus") == "Failed":
            raise RuntimeError(f"function {name} failed: {cfg.get('StateReason') or cfg.get('LastUpdateStatusReason')}")
        if time.time() > deadline:
            raise TimeoutError(f"function {name} not ready after {timeout:.0f}s: {cfg.get('State')}/{cfg.get('LastUpdateStatus')}")
        time.sleep(1)


def _retry(call, attempts: int = 10):
    for attempt in range(attempts):
        try:
            return call()
        except ClientError as exc:
            # A freshly created role or a function still updating is retried, as on AWS.
            retryable = _error_code(exc) in ("ResourceConflictException", "InvalidParameterValueException")
            if not retryable or attempt == attempts - 1:
                raise
            time.sleep(2)


def ensure_function(name: str, *, role_arn: str, runtime: str, handler: str, code: bytes,
                    environment: dict[str, str], timeout: int = 30, memory: int = 128) -> str:
    """Create the function or update its code and configuration. Returns the function ARN."""
    lam = client("lambda")
    config = dict(Role=role_arn, Runtime=runtime, Handler=handler, Timeout=timeout, MemorySize=memory,
                  Environment={"Variables": environment})
    try:
        current = lam.get_function_configuration(FunctionName=name)
    except ClientError as exc:
        if _error_code(exc) != "ResourceNotFoundException":
            raise
        arn = _retry(lambda: lam.create_function(FunctionName=name, Code={"ZipFile": code}, **config))["FunctionArn"]
        log(f"created function {name}")
        wait_function(name)
        return arn
    wait_function(name)
    _retry(lambda: lam.update_function_code(FunctionName=name, ZipFile=code))
    wait_function(name)
    _retry(lambda: lam.update_function_configuration(FunctionName=name, **config))
    wait_function(name)
    log(f"updated function {name}")
    return current["FunctionArn"]


def ensure_sqs_mapping(function: str, source_arn: str, *, batch_size: int | None = None,
                       response_types: list[str] | None = None) -> str:
    """Create or update the event source mapping from `source_arn` to `function`. Returns its UUID."""
    lam = client("lambda")
    settings: dict = {}
    if batch_size is not None:
        settings["BatchSize"] = batch_size
    if response_types is not None:
        settings["FunctionResponseTypes"] = list(response_types)
    existing = [m for m in lam.list_event_source_mappings(FunctionName=function).get("EventSourceMappings", [])
                if m.get("EventSourceArn") == source_arn]
    if existing:
        uuid = existing[0]["UUID"]
        if settings:
            _retry(lambda: lam.update_event_source_mapping(UUID=uuid, **settings))
        return uuid
    uuid = _retry(lambda: lam.create_event_source_mapping(
        FunctionName=function, EventSourceArn=source_arn, **settings))["UUID"]
    log(f"created event source mapping {source_arn.rsplit(':', 1)[-1]} -> {function}")
    return uuid
