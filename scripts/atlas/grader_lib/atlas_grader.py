"""Helpers for atlas-commerce ticket graders (shipped to /tests/atlas/ with every step).

    import os, sys; sys.path.insert(0, os.environ["ATLAS_LIB"])
    import atlas_grader as ag

    ag.client("sqs")                  # the builder identity (ProvisioningOperator), like the agent
    ag.client("sqs", verifier=True)   # the grader-only identity: not restricted by deny-guard
    ag.repo()                         # Path of the grading copy of the agent's /app (read it as data)
    ag.code_sha256(ag.package_dir(ag.repo() / "services/x"))   # what deploy/support.py would upload

Environment set by test.sh: ATLAS_VERIFIER_PROFILE (profile of the grader-only key in
AWS_SHARED_CREDENTIALS_FILE), ATLAS_REPO (grading copy), AWS_ENDPOINT_URL, AWS_DEFAULT_REGION.
"""

from __future__ import annotations

import base64
import hashlib
import io
import os
import zipfile
from pathlib import Path

import boto3
from botocore.config import Config

ENDPOINT = os.environ.get("AWS_ENDPOINT_URL", "http://localstack:4566")
REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
CONFIG = Config(region_name=REGION, retries={"max_attempts": 3, "mode": "standard"},
                read_timeout=90, connect_timeout=10)


def client(service: str, *, verifier: bool = False):
    profile = os.environ["ATLAS_VERIFIER_PROFILE"] if verifier else None
    session = boto3.Session(profile_name=profile, region_name=REGION)
    return session.client(service, endpoint_url=ENDPOINT, config=CONFIG)


def repo() -> Path:
    return Path(os.environ.get("ATLAS_REPO", os.getcwd()))


# Byte-for-byte the packaging rule of the base tree's deploy/support.py (trusted copy: the
# grader never runs the agent's deploy code).
def _zip(entries: list[tuple[str, bytes]]) -> bytes:
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
    directory = Path(directory)
    files = [p for p in directory.rglob("*") if _packaged(p, directory)]
    return _zip([(p.relative_to(directory).as_posix(), p.read_bytes()) for p in files])


def package_files(*paths: Path) -> bytes:
    return _zip([(Path(p).name, Path(p).read_bytes()) for p in paths])


def code_sha256(zip_bytes: bytes) -> str:
    """Lambda's CodeSha256 of a deployment package: base64 of its SHA-256."""
    return base64.b64encode(hashlib.sha256(zip_bytes).digest()).decode()


def drain(sqs, url: str, rounds: int = 5) -> int:
    """Delete whatever is visible in a queue now."""
    removed = 0
    for _ in range(rounds):
        messages = sqs.receive_message(QueueUrl=url, MaxNumberOfMessages=10, WaitTimeSeconds=0).get("Messages", [])
        if not messages:
            break
        sqs.delete_message_batch(QueueUrl=url, Entries=[
            {"Id": str(i), "ReceiptHandle": m["ReceiptHandle"]} for i, m in enumerate(messages)])
        removed += len(messages)
    return removed
