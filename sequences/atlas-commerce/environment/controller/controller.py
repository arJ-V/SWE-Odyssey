#!/usr/bin/env python3
"""atlas-commerce reset controller: the trusted half of each step's setup and verification.

It runs in its own container with NO network (compose `network_mode: none`), so neither the
agent's container nor a Lambda can connect to it. It holds the Docker socket and talks to the
rest of the trial only through:
  - the `ctl` volume (/ctl here, /run/atlas-ctl in main): main drops request files
    `req-<id>.json` = {"id", "op", "token", "data"}; the controller answers `resp-<id>.json`;
  - the `shared` volume (/shared): credentials for main and the proxy allowlists;
  - `docker restart` / `docker rm` / `docker exec` on this compose project's own containers.

Every request needs a step token. The compiled task gives each step a setup token (in that
step's setup.sh) and a verify token (in that step's test.sh, uploaded only after the agent's
turn); /etc/atlas/tokens.json, mounted into this container only, holds their sha256.

ops (token kind, allowed once each, in this order):
  reset            setup   restart LocalStack, the proxy and deny-guard; remove Lambda containers;
                           empty /shared; create ProvisioningOperator with the builder policy and
                           write its key to /shared/agent/credentials and both allowlists
  setup-report     setup   store setup.sh's own timings for the verifier to collect
  verifier-creds   verify  create VerifierOperator and a key that the proxy accepts and
                           deny-guard does not restrict; answer it with this step's record
  verifier-done    verify  revoke that key
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback

CTL = os.environ.get("ATLAS_CTL_DIR", "/ctl")
SHARED = os.environ.get("ATLAS_SHARED_DIR", "/shared")
TOKENS = os.environ.get("ATLAS_TOKENS", "/etc/atlas/tokens.json")
POLICY = os.environ.get("ATLAS_BUILDER_POLICY", "/etc/atlas/builder-policy.json")
PROJECT = os.environ.get("ATLAS_PROJECT", "")
SERVICES = [s for s in os.environ.get("ATLAS_SERVICES", "").split(",") if s]
READY = "/tmp/atlas-controller-ready"
LS_PYTHON = "/opt/code/localstack/.venv/bin/python"
REQ = re.compile(r"^req-([0-9a-f]{32})\.json$")

BUILDER_USER = "ProvisioningOperator"
VERIFIER_USER = "VerifierOperator"
VERIFIER_POLICY = {
    "Version": "2012-10-17",
    "Statement": [{"Sid": "GradeAtlasCommerce", "Effect": "Allow", "Action": "*", "Resource": "*"}],
}

# Runs inside the LocalStack container (its own Python and boto3) against localhost with the
# root test/test credentials, which therefore never leave that container.
_IAM_SCRIPT = r'''
import json, sys, boto3
args = json.loads(sys.argv[1])
iam = boto3.client("iam", endpoint_url="http://localhost:4566", region_name="us-east-1",
                   aws_access_key_id="test", aws_secret_access_key="test")
user = args["user"]
try:
    iam.get_user(UserName=user)
except iam.exceptions.NoSuchEntityException:
    iam.create_user(UserName=user)
for key in iam.list_access_keys(UserName=user).get("AccessKeyMetadata", []):
    iam.delete_access_key(UserName=user, AccessKeyId=key["AccessKeyId"])
if args.get("revoke_only"):
    print(json.dumps({"ok": True}))
    sys.exit(0)
iam.put_user_policy(UserName=user, PolicyName=args["policy_name"], PolicyDocument=args["policy"])
key = iam.create_access_key(UserName=user)["AccessKey"]
print(json.dumps({"access_key": key["AccessKeyId"], "secret_key": key["SecretAccessKey"]}))
'''


def log(message: str) -> None:
    print(f"[atlas-controller {time.strftime('%H:%M:%S')}] {message}", flush=True)


def run(args: list[str], *, timeout: float = 120, input_text: str | None = None, check: bool = True):
    result = subprocess.run(args, input=input_text, capture_output=True, text=True, timeout=timeout)
    if check and result.returncode != 0:
        raise RuntimeError(f"{' '.join(args[:4])} failed ({result.returncode}): {result.stderr.strip()[-500:]}")
    return result


def container(service: str) -> tuple[str, str]:
    """(id, name) of this project's container for a compose service."""
    out = run(["docker", "ps", "-a", "--filter", f"label=com.docker.compose.project={PROJECT}",
               "--filter", f"label=com.docker.compose.service={service}", "--format", "{{.ID}}\t{{.Names}}"]).stdout
    lines = [line.split("\t") for line in out.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError(f"no container for service {service} in project {PROJECT}")
    return lines[0][0], lines[0][1]


def remove_lambda_containers(localstack_name: str) -> list[str]:
    """LocalStack names its Lambda containers <its own container name, '_' as '-'>-lambda-<function>-<hash>."""
    out = run(["docker", "ps", "-a", "--format", "{{.ID}}\t{{.Names}}"]).stdout
    prefixes = (f"{localstack_name}-lambda-", f"{localstack_name.replace('_', '-')}-lambda-")
    victims = [line.split("\t") for line in out.splitlines()
               if "\t" in line and line.split("\t")[1].startswith(prefixes)]
    if victims:
        run(["docker", "rm", "-f", "-v", *[v[0] for v in victims]], timeout=120, check=False)
    return [v[1] for v in victims]


def localstack_health(ls_id: str) -> dict:
    result = run(["docker", "exec", ls_id, "curl", "-sf", "--max-time", "5", "http://localhost:4566/_localstack/health"],
                 timeout=20, check=False)
    if result.returncode != 0:
        return {}
    try:
        return json.loads(result.stdout).get("services", {})
    except ValueError:
        return {}


def wait_localstack(ls_id: str, timeout: float = 240) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        services = localstack_health(ls_id)
        if services and all(services.get(s) in ("available", "running") for s in SERVICES):
            return
        time.sleep(1)
    raise RuntimeError(f"LocalStack not healthy after {timeout:.0f}s")


def wait_proxy(proxy_id: str, timeout: float = 60) -> None:
    probe = "import urllib.request; urllib.request.urlopen('http://localhost:4566/_localstack/health', timeout=5).read()"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if run(["docker", "exec", proxy_id, "python", "-c", probe], timeout=20, check=False).returncode == 0:
            return
        time.sleep(0.5)
    raise RuntimeError(f"credential proxy not healthy after {timeout:.0f}s")


def iam_user(ls_id: str, **args) -> dict:
    result = run(["docker", "exec", ls_id, LS_PYTHON, "-c", _IAM_SCRIPT, json.dumps(args)], timeout=60)
    return json.loads(result.stdout.strip().splitlines()[-1])


def write_atomic(path: str, text: str, mode: int = 0o644) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp-{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def allowlist_path(name: str) -> str:
    return os.path.join(SHARED, "proxy", name)


def read_keys(name: str) -> list[str]:
    try:
        with open(allowlist_path(name), encoding="utf-8") as handle:
            return [line.strip() for line in handle if line.strip()]
    except OSError:
        return []


def write_keys(name: str, keys: list[str]) -> None:
    write_atomic(allowlist_path(name), "".join(k + "\n" for k in keys))


def empty_dir(path: str, keep: set[str] = frozenset()) -> None:
    for entry in os.listdir(path):
        if entry in keep:
            continue
        full = os.path.join(path, entry)
        if os.path.isdir(full) and not os.path.islink(full):
            shutil.rmtree(full, ignore_errors=True)
        else:
            try:
                os.unlink(full)
            except OSError:
                pass


class Controller:
    def __init__(self) -> None:
        with open(TOKENS, encoding="utf-8") as handle:
            self.tokens = json.load(handle)["tokens"]  # sha256(token) -> {"step", "kind"}
        with open(POLICY, encoding="utf-8") as handle:
            self.builder_policy = json.dumps(json.load(handle))
        self.done: set[tuple[str, str]] = set()      # (step, op) already performed
        self.records: dict[str, dict] = {}            # step -> timings and findings
        self.verifier_keys: dict[str, str] = {}

    # --- ops ----------------------------------------------------------------------------------

    def reset(self, step: str, keep: set[str]) -> dict:
        timings: dict[str, float] = {}
        start = mark = time.monotonic()

        def lap(name: str) -> None:
            nonlocal mark
            now = time.monotonic()
            timings[name] = round(now - mark, 2)
            mark = now

        # Revoke first: from here on every old key gets 403 at the proxy.
        os.makedirs(os.path.join(SHARED, "proxy"), exist_ok=True)
        write_keys("allowlist", [])
        write_keys("builder-keys", [])
        ls_id, ls_name = container("localstack-backend")
        proxy_id, _ = container("localstack-proxy")
        guard_id, _ = container("deny-guard")
        removed = remove_lambda_containers(ls_name)
        lap("revoke_and_remove_lambdas")
        run(["docker", "restart", "-t", "3", ls_id], timeout=180)
        wait_localstack(ls_id)
        lap("localstack_restart_to_healthy")
        removed += remove_lambda_containers(ls_name)
        # The proxy caches session attestations for 30 s and both relays may hold open
        # connections from the last step: restart them rather than wait.
        run(["docker", "restart", "-t", "1", proxy_id, guard_id], timeout=120)
        lap("proxy_and_guard_restart")
        empty_dir(SHARED)
        builder = iam_user(ls_id, user=BUILDER_USER, policy_name="atlas-commerce-builder", policy=self.builder_policy)
        write_atomic(os.path.join(SHARED, "agent", "credentials"),
                     "[default]\n"
                     f"aws_access_key_id = {builder['access_key']}\n"
                     f"aws_secret_access_key = {builder['secret_key']}\n"
                     "region = us-east-1\n")
        write_keys("builder-keys", [builder["access_key"]])
        write_keys("allowlist", [builder["access_key"]])
        lap("credential_bootstrap")
        wait_proxy(proxy_id)
        lap("proxy_healthy")
        empty_dir(CTL, keep)
        timings["total"] = round(time.monotonic() - start, 2)
        self.records[step] = {"step": step, "reset": timings, "lambda_containers_removed": removed}
        self.verifier_keys.pop(step, None)
        log(f"reset for {step}: {timings} removed {len(removed)} Lambda container(s)")
        return {"ok": True, "timings": timings, "lambda_containers_removed": removed}

    def setup_report(self, step: str, data: dict) -> dict:
        self.records.setdefault(step, {"step": step})["setup"] = data
        return {"ok": True}

    def verifier_creds(self, step: str) -> dict:
        ls_id, _ = container("localstack-backend")
        key = iam_user(ls_id, user=VERIFIER_USER, policy_name="atlas-commerce-verifier",
                       policy=json.dumps(VERIFIER_POLICY))
        self.verifier_keys[step] = key["access_key"]
        # The proxy accepts the key; deny-guard only restricts keys in builder-keys.
        write_keys("allowlist", read_keys("allowlist") + [key["access_key"]])
        log(f"verifier key issued for {step}")
        return {"ok": True, "access_key": key["access_key"], "secret_key": key["secret_key"],
                "record": self.records.get(step, {"step": step})}

    def verifier_done(self, step: str) -> dict:
        key = self.verifier_keys.pop(step, None)
        if key:
            write_keys("allowlist", [k for k in read_keys("allowlist") if k != key])
            ls_id, _ = container("localstack-backend")
            iam_user(ls_id, user=VERIFIER_USER, revoke_only=True)
        log(f"verifier key revoked for {step}")
        return {"ok": True}

    # --- request handling ---------------------------------------------------------------------

    OPS = {"reset": "setup", "setup-report": "setup", "verifier-creds": "verify", "verifier-done": "verify"}
    PREREQ = {"setup-report": "reset", "verifier-done": "verifier-creds"}

    def handle(self, request: dict, keep: set[str]) -> dict:
        op = request.get("op")
        token = request.get("token")
        if op not in self.OPS or not isinstance(token, str):
            return {"ok": False, "error": "bad request"}
        entry = self.tokens.get(hashlib.sha256(token.encode()).hexdigest())
        if not entry or entry.get("kind") != self.OPS[op]:
            return {"ok": False, "error": "invalid token"}
        step = entry["step"]
        if (step, op) in self.done:
            return {"ok": False, "error": f"{op} already done for this step"}
        if op in self.PREREQ and (step, self.PREREQ[op]) not in self.done:
            return {"ok": False, "error": f"{op} before {self.PREREQ[op]}"}
        if op == "reset":
            answer = self.reset(step, keep)
        elif op == "setup-report":
            data = request.get("data")
            answer = self.setup_report(step, data if isinstance(data, dict) else {})
        elif op == "verifier-creds":
            answer = self.verifier_creds(step)
        else:
            answer = self.verifier_done(step)
        self.done.add((step, op))
        return answer

    def poll_once(self) -> None:
        for name in sorted(os.listdir(CTL)):
            match = REQ.match(name)
            if not match:
                continue
            path = os.path.join(CTL, name)
            request_id = match.group(1)
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
                with os.fdopen(fd, "rb") as handle:
                    raw = handle.read(65536)
                os.unlink(path)
                request = json.loads(raw)
                if not isinstance(request, dict):
                    raise ValueError("request is not an object")
            except (OSError, ValueError) as exc:
                answer = {"ok": False, "error": f"unreadable request: {exc}"}
                request = {}
            else:
                resp_name = f"resp-{request_id}.json"
                try:
                    answer = self.handle(request, keep={resp_name})
                except Exception as exc:  # report, never die
                    log(f"{request.get('op')} failed: {exc}\n{traceback.format_exc()}")
                    answer = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            log(f"request {request_id[:8]} op={request.get('op')} ok={answer.get('ok')}"
                + ("" if answer.get("ok") else f" error={answer.get('error')}"))
            # Written under a temporary name and renamed, so the reader never sees half a file.
            resp = os.path.join(CTL, f"resp-{request_id}.json")
            tmp = os.path.join(CTL, f".resp-{request_id}.tmp")
            for stale in (resp, tmp):
                try:
                    os.unlink(stale)
                except FileNotFoundError:
                    pass
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(answer, handle)
            os.replace(tmp, resp)


def main() -> int:
    if not PROJECT or not SERVICES:
        log("ATLAS_PROJECT and ATLAS_SERVICES must be set")
        return 2
    os.makedirs(CTL, exist_ok=True)
    controller = Controller()
    run(["docker", "version", "--format", "{{.Server.Version}}"], timeout=30)
    with open(READY, "w", encoding="utf-8") as handle:
        handle.write("ready\n")
    log(f"ready: project {PROJECT}, {len(controller.tokens)} step tokens, services {','.join(SERVICES)}")
    while True:
        try:
            controller.poll_once()
        except Exception as exc:
            log(f"poll error: {exc}")
        time.sleep(0.2)


if __name__ == "__main__":
    sys.exit(main())
