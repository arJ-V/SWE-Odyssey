#!/usr/bin/env python3
"""Fail-closed LocalStack credential guard."""
import os
import re
import select
import socket
import threading
from urllib.parse import urlsplit

_UPSTREAM = urlsplit(
    os.environ.get("LOCALSTACK_UPSTREAM", "http://localstack-backend:4566")
)
UPSTREAM_HOST = _UPSTREAM.hostname or "localstack-backend"
UPSTREAM_PORT = _UPSTREAM.port or 4566
LISTEN_PORT = int(os.environ.get("PROXY_PORT", "4566"))
ALLOWLIST_FILE = os.environ.get("PROXY_ALLOWLIST", "/shared/proxy/allowlist")
_SESSION_PREFIXES = ("LSIA", "ASIA")
_allow_cache = {"mtime": None, "keys": set()}


def _allowlist():
    try:
        stat = os.stat(ALLOWLIST_FILE)
    except OSError:
        return set()
    if _allow_cache["mtime"] != stat.st_mtime:
        try:
            with open(ALLOWLIST_FILE, encoding="utf-8") as allowlist:
                _allow_cache["keys"] = {
                    line.strip() for line in allowlist if line.strip()
                }
            _allow_cache["mtime"] = stat.st_mtime
        except OSError:
            return set()
    return _allow_cache["keys"]


def _access_key(authorization, query):
    if authorization:
        match = re.search(r"Credential=([^/,\s]+)", authorization)
        if match:
            return match.group(1)
    match = re.search(
        r"(?:^|&)(?:X-Amz-Credential|AWSAccessKeyId)=([^/&%]+)", query
    )
    return match.group(1) if match else None


# LocalStack does not validate SigV4 secrets and maps unknown credentials to the
# root principal. Prefix + token checks are therefore insufficient: an agent can
# forge an LSIA-shaped key and arbitrary token. Attest session keys against the
# backend STS identity map and accept only credentials that resolve to an
# assumed-role ARN. Successful attestations are cached briefly.
_SESSION_ATTEST_CACHE = {}


def _session_token(headers, query):
    token = headers.get("x-amz-security-token")
    if token:
        return token
    from urllib.parse import parse_qs

    for key, values in parse_qs(query, keep_blank_values=True).items():
        if key.lower() == "x-amz-security-token" and values:
            return values[0]
    return None


def _attested_session_arn(access_key, token):
    if not access_key or not token or access_key[:4] not in ("LSIA", "ASIA"):
        return None

    import http.client
    import time
    from urllib.parse import urlsplit

    now = time.monotonic()
    cached = _SESSION_ATTEST_CACHE.get(access_key)
    if cached and cached[0] > now:
        return cached[1]

    upstream = urlsplit(
        os.environ.get("LOCALSTACK_UPSTREAM", "http://localstack-backend:4566")
    )
    host = upstream.hostname or "localstack-backend"
    port = upstream.port or 4566
    body = "Action=GetCallerIdentity&Version=2011-06-15"
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    day = stamp[:8]
    authorization = (
        "AWS4-HMAC-SHA256 "
        f"Credential={access_key}/{day}/us-east-1/sts/aws4_request, "
        "SignedHeaders=host;x-amz-date;x-amz-security-token, "
        f"Signature={'0' * 64}"
    )
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.request(
            "POST",
            "/",
            body=body,
            headers={
                "Authorization": authorization,
                "Content-Type": "application/x-www-form-urlencoded",
                "Host": f"{host}:{port}",
                "X-Amz-Date": stamp,
                "X-Amz-Security-Token": token,
            },
        )
        response = conn.getresponse()
        payload = response.read()
    except OSError:
        return None
    finally:
        conn.close()
    if response.status != 200:
        return None

    match = re.search(rb"<Arn>([^<]+)</Arn>", payload)
    if not match:
        match = re.search(rb'"Arn"\s*:\s*"([^"]+)"', payload)
    arn = match.group(1).decode("utf-8", "replace") if match else ""
    if not re.fullmatch(
        r"arn:[^:]+:sts::000000000000:assumed-role/[^/]+/.+", arn
    ):
        return None
    _SESSION_ATTEST_CACHE[access_key] = (now + 30, arn)
    return arn


def _untrusted_session(headers, query):
    authorization = headers.get("authorization", "")
    match = re.search(r"Credential=([^/,\s]+)", authorization)
    if not match:
        match = re.search(
            r"(?:^|&)(?:X-Amz-Credential|AWSAccessKeyId)=([^/&%]+)", query
        )
    access_key = match.group(1) if match else None
    token = _session_token(headers, query)
    if not access_key or access_key[:4] not in ("LSIA", "ASIA") or not token:
        return False
    return _attested_session_arn(access_key, token) is None


def _should_block(headers, query):
    if _untrusted_session(headers, query):
        return True
    access_key = _access_key(headers.get("authorization", ""), query)
    if access_key is None:
        return True
    if access_key in _allowlist():
        return False
    has_token = (
        "x-amz-security-token" in headers or "X-Amz-Security-Token" in query
    )
    return not (
        has_token
        and access_key.startswith(_SESSION_PREFIXES)
    )


def _relay(left, right):
    sockets = [left, right]
    try:
        while True:
            ready, _, failed = select.select(sockets, [], sockets, 120)
            if failed or not ready:
                return
            for source in ready:
                data = source.recv(65536)
                if not data:
                    return
                (right if source is left else left).sendall(data)
    except OSError:
        return


def _localstack_control_path_blocked(method, target):
    path = urlsplit(target).path
    return path.startswith("/_localstack/") and not (
        path == "/_localstack/health" and method.upper() in {"GET", "HEAD"}
    )


def _handle(client):
    client.settimeout(120)
    buffered = b""
    try:
        while b"\r\n\r\n" not in buffered:
            chunk = client.recv(65536)
            if not chunk:
                return
            buffered += chunk
            if len(buffered) > 1_048_576:
                return
    except OSError:
        return

    header_bytes, _, body_start = buffered.partition(b"\r\n\r\n")
    lines = header_bytes.split(b"\r\n")
    try:
        _method, path, _version = lines[0].decode("latin1").split(" ", 2)
        if _localstack_control_path_blocked(_method, path):
            payload = (
                b'{"Error":{"Code":"AccessDenied","Message":'
                b'"LocalStack administrative route blocked"}}'
            )
            client.sendall(
                b"HTTP/1.1 403 Forbidden\r\nContent-Type: application/json\r\n"
                b"Content-Length: "
                + str(len(payload)).encode()
                + b"\r\nConnection: close\r\n\r\n"
                + payload
            )
            return
    except ValueError:
        return
    headers = {}
    for line in lines[1:]:
        if b":" in line:
            key, value = line.split(b":", 1)
            headers[key.decode("latin1").strip().lower()] = (
                value.decode("latin1").strip()
            )

    request = urlsplit(path)
    is_health = request.path == "/_localstack/health"
    is_public_api = (
        request.path.startswith("/_aws/execute-api/")
        or (
            request.path.startswith("/restapis/")
            and "/_user_request_" in request.path
        )
    )
    if not (is_health or is_public_api) and _should_block(headers, request.query):
        body = (
            b'{"Error":{"Code":"AccessDenied","Message":'
            b'"Credential blocked by the LocalStack guard"}}'
        )
        client.sendall(
            b"HTTP/1.1 403 Forbidden\r\nContent-Type: application/json\r\n"
            b"Content-Length: "
            + str(len(body)).encode()
            + b"\r\nConnection: close\r\n\r\n"
            + body
        )
        return

    try:
        upstream = socket.create_connection(
            (UPSTREAM_HOST, UPSTREAM_PORT), timeout=120
        )
    except OSError:
        return
    try:
        outgoing = [lines[0]]
        for line in lines[1:]:
            lower = line.lower()
            if lower.startswith(b"connection:") or lower.startswith(
                b"proxy-connection:"
            ):
                continue
            outgoing.append(line)
        outgoing.append(b"Connection: close")
        upstream.sendall(
            b"\r\n".join(outgoing) + b"\r\n\r\n" + body_start
        )
        _relay(client, upstream)
    finally:
        upstream.close()


def main():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", LISTEN_PORT))
    server.listen(128)
    while True:
        connection, _ = server.accept()
        threading.Thread(
            target=_handle, args=(connection,), daemon=True
        ).start()


if __name__ == "__main__":
    main()
