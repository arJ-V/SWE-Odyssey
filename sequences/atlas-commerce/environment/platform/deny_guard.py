#!/usr/bin/env python3
"""Proxy-side enforcement of the builder policy's explicit Deny statements.

LocalStack Community 4.14.0 has no IAM enforcement, so nothing stops the
agent's ProvisioningOperator identity from writing graded data directly (for
example DynamoDB PutItem on the table the verifier reads). Here the credential
proxy, which already sees every agent request, applies the unconditional Deny
statements of builder-policy.json to requests signed with an allowlisted
builder access key.

Scope (deliberately narrow):
- only Deny statements without Condition / NotAction / NotResource;
- only requests signed with a key listed in the proxy allowlist (the builder);
  assumed-role sessions are not checked;
- the Allow side of the builder policy is NOT enforced (no implicit deny).
"""

import json
import os
import re
from urllib.parse import parse_qs, unquote, urlsplit

POLICY_FILE = os.environ.get("BUILDER_DENY_POLICY", "/proxy/builder-policy.json")
ACCOUNT = "000000000000"
MAX_BODY = 8 * 1024 * 1024
_cache = {"mtime": None, "rules": []}


def _as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _rules():
    try:
        mtime = os.stat(POLICY_FILE).st_mtime
    except OSError:
        return []
    if _cache["mtime"] != mtime:
        rules, allows = [], []
        try:
            with open(POLICY_FILE, encoding="utf-8") as handle:
                doc = json.load(handle)
            for st in _as_list(doc.get("Statement")):
                if st.get("Effect") == "Allow":
                    # Lenient: a conditional or Not* Allow counts as covering everything it names.
                    allows.append((_as_list(st.get("Action")) or ["*"] if "NotAction" not in st else ["*"],
                                   _as_list(st.get("Resource")) or ["*"] if "NotResource" not in st else ["*"]))
                    continue
                if st.get("Effect") != "Deny":
                    continue
                if any(k in st for k in ("Condition", "NotAction", "NotResource")):
                    continue
                rules.append((_as_list(st.get("Action")), _as_list(st.get("Resource")), st.get("Sid", "")))
        except (OSError, ValueError):
            rules, allows = [], []
        _cache.update(mtime=mtime, rules=rules, allows=allows)
    return _cache["rules"]


# Verifier fix (2026-11-10): actions that the builder policy's Allow side must cover, as on
# LocalStack Pro with ENFORCE_IAM. Without this, the builder key could assume a role it
# created (no pool builder policy grants sts:AssumeRole) and write graded data with the
# session key, which deny-guard does not inspect; PartiQL writes were not checked at all.
IMPLICIT_DENY_UNLESS_ALLOWED = (
    "sts:AssumeRole", "sts:AssumeRoleWithWebIdentity", "sts:AssumeRoleWithSAML",
    "dynamodb:PartiQLSelect", "dynamodb:PartiQLInsert", "dynamodb:PartiQLUpdate",
    "dynamodb:PartiQLDelete",
)


def _not_allowed(action, resources):
    if not any(action.lower() == a.lower() for a in IMPLICIT_DENY_UNLESS_ALLOWED):
        return None
    _rules()
    for actions, patterns in _cache.get("allows", []):
        if not any(_wild(a, action, True) for a in actions):
            continue
        if not resources or any(_wild(p, r, False) for r in resources for p in patterns):
            return None
    return "implicit-deny (not allowed by the builder policy)"


def _partiql(op, body, region):
    """(iam_action, [table_arn]) pairs for ExecuteStatement / BatchExecuteStatement / ExecuteTransaction."""
    stmts = []
    if op == "ExecuteStatement":
        stmts = [body.get("Statement") or ""]
    elif op == "BatchExecuteStatement":
        stmts = [s.get("Statement") or "" for s in body.get("Statements") or []]
    elif op == "ExecuteTransaction":
        stmts = [s.get("Statement") or "" for s in body.get("TransactStatements") or []]
    out = []
    for text in stmts:
        verb = (text.strip().split(None, 1) or ["SELECT"])[0].upper()
        action = {"SELECT": "dynamodb:PartiQLSelect", "INSERT": "dynamodb:PartiQLInsert",
                  "UPDATE": "dynamodb:PartiQLUpdate", "DELETE": "dynamodb:PartiQLDelete",
                  "EXISTS": "dynamodb:PartiQLSelect"}.get(verb, "dynamodb:PartiQLUpdate")
        m = re.search(r'(?:\bFROM\b|\bINTO\b|^\s*UPDATE\b)\s+"?([A-Za-z0-9_.\-]+)"?', text, re.IGNORECASE)
        table = m.group(1) if m else "*"
        write = {"dynamodb:PartiQLInsert": "dynamodb:PutItem", "dynamodb:PartiQLUpdate": "dynamodb:UpdateItem",
                 "dynamodb:PartiQLDelete": "dynamodb:DeleteItem"}.get(action)
        out.append((action, write, [_ddb_arn(region, table)]))
    return out


def _wild(pattern, value, ignore_case):
    regex = "".join(".*" if c == "*" else "." if c == "?" else re.escape(c) for c in pattern)
    return re.fullmatch(regex, value, re.IGNORECASE if ignore_case else 0) is not None


def _denied(action, resources):
    for actions, patterns, sid in _rules():
        if not any(_wild(a, action, True) for a in actions):
            continue
        for res in resources:
            if any(_wild(p, res, False) for p in patterns):
                return sid or "explicit-deny"
    return None


def _scope(headers, query):
    auth = headers.get("authorization", "")
    m = re.search(r"Credential=[^/]+/\d{8}/([^/]+)/([^/]+)/aws4_request", auth)
    if not m:
        cred = parse_qs(query).get("X-Amz-Credential", [""])[0]
        m = re.search(r"[^/]+/\d{8}/([^/]+)/([^/]+)/aws4_request", unquote(cred))
    return (m.group(1), m.group(2)) if m else ("us-east-1", "")


def _sqs_arn(region, url):
    parts = [p for p in urlsplit(url).path.split("/") if p]
    if not parts:
        return None
    acct = parts[-2] if len(parts) >= 2 and re.fullmatch(r"\d{12}", parts[-2]) else ACCOUNT
    return f"arn:aws:sqs:{region}:{acct}:{parts[-1]}"


def _ddb_arn(region, name):
    return name if name.startswith("arn:") else f"arn:aws:dynamodb:{region}:{ACCOUNT}:table/{name}"


def _json_resources(service, op, body, region):
    res = []
    if service == "dynamodb":
        if body.get("TableName"):
            res.append(_ddb_arn(region, body["TableName"]))
        for name in (body.get("RequestItems") or {}):
            res.append(_ddb_arn(region, name))
        for item in body.get("TransactItems") or []:
            for inner in item.values():
                if isinstance(inner, dict) and inner.get("TableName"):
                    res.append(_ddb_arn(region, inner["TableName"]))
    elif service == "sqs":
        if body.get("QueueUrl"):
            res.append(_sqs_arn(region, body["QueueUrl"]))
    elif service == "kms":
        key = body.get("KeyId") or ""
        if key.startswith("arn:"):
            res.append(key)
        elif key.startswith("alias/"):
            res.append(f"arn:aws:kms:{region}:{ACCOUNT}:{key}")
            res.append(f"arn:aws:kms:{region}:{ACCOUNT}:key/unresolved-alias")
        elif key:
            res.append(f"arn:aws:kms:{region}:{ACCOUNT}:key/{key}")
        else:  # Decrypt without KeyId: the key is inside the ciphertext.
            res.append(f"arn:aws:kms:{region}:{ACCOUNT}:key/from-ciphertext")
    elif service == "secretsmanager":
        sid = body.get("SecretId") or ""
        if sid.startswith("arn:"):
            res.append(sid)
        elif sid:
            res.append(f"arn:aws:secretsmanager:{region}:{ACCOUNT}:secret:{sid}-XXXXXX")
    elif service == "kinesis":
        if body.get("StreamARN"):
            res.append(body["StreamARN"])
        if body.get("StreamName"):
            res.append(f"arn:aws:kinesis:{region}:{ACCOUNT}:stream/{body['StreamName']}")
    elif service == "states":
        for k in ("stateMachineArn", "executionArn"):
            if body.get(k):
                res.append(body[k])
    return [r for r in res if r]


def _form_resources(service, params, region, target="/"):
    get = lambda k: (params.get(k) or [""])[0]
    res = []
    if service == "sqs" and get("QueueUrl"):
        res.append(_sqs_arn(region, get("QueueUrl")))
    elif service == "sqs" and len([p for p in urlsplit(target).path.split("/") if p]) >= 2:
        # Query request POSTed to the queue URL itself (no QueueUrl parameter).
        res.append(_sqs_arn(region, urlsplit(target).path))
    elif service == "sts" and get("RoleArn"):
        res.append(get("RoleArn"))
    elif service == "sns":
        for k in ("TopicArn", "TargetArn"):
            if get(k):
                res.append(get(k))
    elif service == "iam":
        if get("RoleName"):
            res.append(f"arn:aws:iam::{ACCOUNT}:role/{get('RoleName')}")
        if get("UserName"):
            res.append(f"arn:aws:iam::{ACCOUNT}:user/{get('UserName')}")
        if get("PolicyArn"):
            res.append(get("PolicyArn"))
    return [r for r in res if r]


def _s3_request(method, target, headers):
    url = urlsplit(target)
    host = (headers.get("host") or "").split(":")[0]
    path = unquote(url.path)
    bucket, key = None, ""
    first = host.split(".")[0] if host else ""
    if host and "." in host and first not in ("localstack", "localhost", "s3", "localstack-backend") and not re.fullmatch(r"[\d.]+", host):
        bucket, key = first, path.lstrip("/")
    else:
        parts = path.lstrip("/").split("/", 1)
        bucket = parts[0] or None
        key = parts[1] if len(parts) > 1 else ""
    if not bucket or not key:
        return None, []
    sub = set(parse_qs(url.query, keep_blank_values=True))
    if sub & {"tagging", "acl", "uploads", "uploadId", "retention", "legal-hold", "attributes"}:
        suffix = "Tagging" if "tagging" in sub else None
    else:
        suffix = ""
    if suffix is None:
        return None, []
    action = {"GET": "s3:GetObject", "HEAD": "s3:GetObject", "PUT": "s3:PutObject", "DELETE": "s3:DeleteObject"}.get(method.upper())
    if not action:
        return None, []
    if suffix:
        action = {"s3:GetObject": "s3:GetObjectTagging", "s3:PutObject": "s3:PutObjectTagging", "s3:DeleteObject": "s3:DeleteObjectTagging"}[action]
    return action, [f"arn:aws:s3:::{bucket}/{key}"]


def _lambda_request(method, target, region):
    m = re.match(r"^/2015-03-31/functions/([^/]+)/(invocations|invoke-async)/?$", unquote(urlsplit(target).path))
    if not m or method.upper() != "POST":
        return None, []
    name = m.group(1)
    if not name.startswith("arn:"):
        name = f"arn:aws:lambda:{region}:{ACCOUNT}:function:{name}"
    base = re.sub(r"(:function:[^:]+):[^:]+$", r"\1", name)
    return "lambda:InvokeFunction", sorted({name, base})


def _response(kind, sid, action):
    msg = (f"User is not authorized to perform: {action} with an explicit deny in an "
           f"identity-based policy ({sid}); access denied by the credential guard")
    if kind == "json":
        body = json.dumps({"__type": "AccessDeniedException", "message": msg, "Message": msg}).encode()
        headers = (b"Content-Type: application/x-amz-json-1.0\r\nx-amzn-ErrorType: AccessDeniedException\r\n"
                   b"x-amzn-query-error: AccessDenied;Sender\r\n")
    elif kind == "s3":
        body = (f"<?xml version=\"1.0\" encoding=\"UTF-8\"?><Error><Code>AccessDenied</Code>"
                f"<Message>{msg}</Message></Error>").encode()
        headers = b"Content-Type: application/xml\r\n"
    else:
        body = (f"<ErrorResponse><Error><Type>Sender</Type><Code>AccessDenied</Code>"
                f"<Message>{msg}</Message></Error><RequestId>credential-guard</RequestId></ErrorResponse>").encode()
        headers = b"Content-Type: text/xml\r\n"
    return (b"HTTP/1.1 403 Forbidden\r\n" + headers + b"Content-Length: " + str(len(body)).encode()
            + b"\r\nConnection: close\r\n\r\n" + body)


def check(client, method, target, headers, body_prefix, access_key, builder_keys):
    """Return (denial_response_or_None, body_prefix_to_forward)."""
    if not access_key or access_key not in builder_keys or not _rules():
        return None, body_prefix
    region, service = _scope(headers, urlsplit(target).query)
    if service == "s3":
        action, res = _s3_request(method, target, headers)
        sid = _denied(action, res) if action else None
        return (_response("s3", sid, action) if sid else None), body_prefix
    if service == "lambda":
        action, res = _lambda_request(method, target, region)
        sid = _denied(action, res) if action else None
        return (_response("json", sid, action) if sid else None), body_prefix
    try:
        length = int(headers.get("content-length", "0"))
    except ValueError:
        length = 0
    if "chunked" in headers.get("transfer-encoding", "").lower():
        # Verifier fix: a chunked JSON/query body was relayed unchecked. Fail closed for the
        # builder key; AWS CLI/SDK JSON and query requests always carry Content-Length.
        kind = "json" if headers.get("x-amz-target") else "query"
        return _response(kind, "chunked-body-not-inspected", f"{service}:*"), body_prefix
    if length <= 0 or length > MAX_BODY:
        return None, body_prefix
    body = body_prefix
    while len(body) < length:
        chunk = client.recv(min(65536, length - len(body)))
        if not chunk:
            break
        body += chunk
    amz_target = headers.get("x-amz-target", "")
    try:
        if amz_target:
            op = amz_target.split(".")[-1]
            payload = json.loads(body.decode("utf-8") or "{}")
            if service == "dynamodb" and op in ("ExecuteStatement", "BatchExecuteStatement", "ExecuteTransaction"):
                for action, write, res in _partiql(op, payload, region):
                    sid = (_denied(action, res) or (write and _denied(write, res))
                           or _not_allowed(action, res))
                    if sid:
                        return _response("json", sid, action), body
                return None, body
            action = f"{service}:{op}"
            res = _json_resources(service, op, payload, region)
            sid = _denied(action, res) or _not_allowed(action, res)
            return (_response("json", sid, action) if sid else None), body
        params = parse_qs(body.decode("utf-8"), keep_blank_values=True)
        op = (params.get("Action") or [""])[0]
        if op:
            action = f"{service}:{op}"
            res = _form_resources(service, params, region, target)
            sid = _denied(action, res) or _not_allowed(action, res)
            return (_response("query", sid, action) if sid else None), body
    except (ValueError, UnicodeDecodeError):
        pass
    return None, body
