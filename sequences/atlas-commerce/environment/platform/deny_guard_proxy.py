#!/usr/bin/env python3
"""Relay between the task's credential proxy and LocalStack that applies deny_guard.

Request path: localstack-proxy -> deny-guard -> localstack-backend.
Requests signed with the builder key (read from the proxy allowlist) are checked
against the unconditional Deny statements of the task's builder policy; every
other request is relayed unchanged. One request per connection, as the task's
own proxy already forces "Connection: close".
"""
import os
import re
import select
import socket
import threading
from urllib.parse import urlsplit

import deny_guard

_UP = urlsplit(os.environ.get("GUARD_UPSTREAM", "http://localstack-backend:4566"))
UP_HOST, UP_PORT = _UP.hostname or "localstack-backend", _UP.port or 4566
ALLOWLIST = os.environ.get("PROXY_ALLOWLIST", "/shared/proxy/allowlist")


def _builder_keys():
    try:
        with open(ALLOWLIST, encoding="utf-8") as handle:
            return {line.strip() for line in handle if line.strip()}
    except OSError:
        return set()


def _access_key(headers, query):
    m = re.search(r"Credential=([^/,\s]+)", headers.get("authorization", ""))
    if not m:
        m = re.search(r"(?:^|&)(?:X-Amz-Credential|AWSAccessKeyId)=([^/&%]+)", query)
    return m.group(1) if m else None


def _relay(left, right):
    socks = [left, right]
    try:
        while True:
            readable, _, broken = select.select(socks, [], socks, 420)
            if broken or not readable:
                return
            for src in readable:
                data = src.recv(65536)
                if not data:
                    return
                (right if src is left else left).sendall(data)
    except OSError:
        return


def _handle(client):
    client.settimeout(120)
    buf = b""
    try:
        while b"\r\n\r\n" not in buf:
            chunk = client.recv(65536)
            if not chunk:
                return
            buf += chunk
            if len(buf) > 1_048_576:
                return
        head, _, body = buf.partition(b"\r\n\r\n")
        lines = head.split(b"\r\n")
        method, target, _ = lines[0].decode("latin1").split(" ", 2)
        headers = {}
        for line in lines[1:]:
            if b":" in line:
                k, v = line.split(b":", 1)
                headers[k.decode("latin1").strip().lower()] = v.decode("latin1").strip()
        try:
            denial, body = deny_guard.check(
                client, method, target, headers, body,
                _access_key(headers, urlsplit(target).query), _builder_keys())
        except Exception as exc:  # fail open on guard bugs, but say so
            print(f"[deny-guard] check error: {exc!r}", flush=True)
            denial = None
        if denial:
            print(f"[deny-guard] denied {method} {target[:120]}", flush=True)
            client.sendall(denial)
            return
        upstream = socket.create_connection((UP_HOST, UP_PORT), timeout=120)
    except (OSError, ValueError):
        return
    try:
        out = [lines[0]] + [l for l in lines[1:] if not l.lower().startswith((b"connection:", b"proxy-connection:"))]
        out.append(b"Connection: close")
        upstream.sendall(b"\r\n".join(out) + b"\r\n\r\n" + body)
        _relay(client, upstream)
    finally:
        upstream.close()
        client.close()


def main():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", 4566))
    server.listen(128)
    while True:
        conn, _ = server.accept()
        threading.Thread(target=_handle, args=(conn,), daemon=True).start()


if __name__ == "__main__":
    main()
