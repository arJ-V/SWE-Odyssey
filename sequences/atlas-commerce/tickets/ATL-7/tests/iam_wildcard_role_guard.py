#!/usr/bin/env python3
"""Fail when an agent-writable role retains universal role permissions.

Every ticket's grader carries an identical copy of this file and a scope file
(iam_wildcard_role_scope.json) beside it that lists which role ARNs count as
agent-writable.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote


CONFIG_NAME = "iam_wildcard_role_scope.json"


def _items(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if value is None:
        return []
    return [str(value)]


def _covers_every_nonempty_string(pattern: str, minimum_length: int = 1) -> bool:
    """Whether an IAM glob covers every non-empty string.

    A literal constrains the language. With only ``*`` and ``?``, a pattern is
    universal for a domain when it cannot require more characters than that
    domain's shortest value.
    """

    return (
        bool(pattern)
        and "*" in pattern
        and set(pattern) <= {"*", "?"}
        and pattern.count("?") <= minimum_length
    )


def action_pattern_is_universal(pattern: str) -> bool:
    pattern = pattern.casefold()
    # Every IAM action has at least the syntactic a:b service/action shape.
    # Account for that domain rather than comparing against arbitrary one-byte
    # strings; e.g. *?*?* covers every possible IAM action.
    if _covers_every_nonempty_string(pattern, minimum_length=3):
        return True
    # IAM actions have exactly the service:operation shape, so *:* is just as
    # universal as * for the Action field.
    parts = pattern.split(":")
    return len(parts) == 2 and all(_covers_every_nonempty_string(part) for part in parts)


def resource_pattern_is_universal(pattern: str) -> bool:
    # Resource identifiers are non-empty but are not necessarily ARNs.
    return _covers_every_nonempty_string(pattern)


def statement_is_universal_allow(statement: Any) -> bool:
    """Return true for one Allow statement granting every action/resource pair."""

    if not isinstance(statement, dict):
        return False
    if str(statement.get("Effect", "")).casefold() != "allow":
        return False

    action_is_universal = any(
        action_pattern_is_universal(value) for value in _items(statement.get("Action"))
    )
    resource_is_universal = any(
        resource_pattern_is_universal(value)
        for value in _items(statement.get("Resource"))
    )

    # Allow + NotAction on every resource means "everything except ...";
    # universal Action + NotResource similarly means "everywhere except ...".
    # Neither is acceptable for this coarse, corpus-wide administrative guard.
    near_universal_action = "NotAction" in statement and bool(
        _items(statement.get("NotAction"))
    )
    near_universal_resource = "NotResource" in statement and bool(
        _items(statement.get("NotResource"))
    )
    return (action_is_universal or near_universal_action) and (
        resource_is_universal or near_universal_resource
    )


def policy_has_universal_allow(document: Any) -> bool:
    if not isinstance(document, dict):
        return False
    statements = document.get("Statement", [])
    if isinstance(statements, dict):
        statements = [statements]
    if not isinstance(statements, list):
        return False
    return any(statement_is_universal_allow(statement) for statement in statements)


def decode_policy_document(document: Any) -> dict[str, Any]:
    """Normalize the object and RFC 3986-encoded forms returned by IAM clients."""

    if isinstance(document, dict):
        return document
    if isinstance(document, str):
        try:
            value = json.loads(unquote(document))
        except json.JSONDecodeError as exc:
            raise RuntimeError("get-role-policy returned an invalid PolicyDocument") from exc
        if isinstance(value, dict):
            return value
    raise RuntimeError("get-role-policy returned a non-object PolicyDocument")


def _option(arguments: list[str], name: str) -> str:
    try:
        return arguments[arguments.index(name) + 1]
    except (ValueError, IndexError) as exc:
        raise RuntimeError(f"missing required IAM argument {name}") from exc


def _iam_client():
    """Create an IAM SDK client without depending on a working AWS CLI.

    Some images contain a distro AWS CLI plus a newer pip
    botocore.  Invoking ``aws`` in those images crashes while constructing its
    command table (``KeyError: opsworkscm``), even though the SDK is healthy.
    Prefer boto3/botocore and retain the CLI only as a compatibility fallback
    for images that expose no importable SDK.
    """

    endpoint = os.environ.get("AWS_ENDPOINT_URL", "").strip() or None
    region = (
        os.environ.get("AWS_REGION", "").strip()
        or os.environ.get("AWS_DEFAULT_REGION", "").strip()
        or "us-east-1"
    )
    try:
        import boto3
    except ImportError:
        try:
            from botocore.session import get_session
        except ImportError:
            raise
        session = get_session()
        session.set_config_variable("region", region)
        return session.create_client("iam", endpoint_url=endpoint, region_name=region)
    return boto3.client("iam", endpoint_url=endpoint, region_name=region)


def _sdk_json(arguments: list[str]) -> dict[str, Any]:
    if len(arguments) < 2 or arguments[0] != "iam":
        raise RuntimeError("role guard only supports IAM SDK operations")

    operation = arguments[1].replace("-", "_")
    kwargs: dict[str, str] = {}
    option_map = {
        "--role-name": "RoleName",
        "--policy-name": "PolicyName",
        "--policy-arn": "PolicyArn",
        "--version-id": "VersionId",
    }
    for option, key in option_map.items():
        if option in arguments:
            kwargs[key] = _option(arguments, option)

    client = _iam_client()
    collection_keys = {
        "list_roles": "Roles",
        "list_role_policies": "PolicyNames",
        "list_attached_role_policies": "AttachedPolicies",
    }
    if operation in collection_keys:
        key = collection_keys[operation]
        values: list[Any] = []
        for page in client.get_paginator(operation).paginate(**kwargs):
            page_values = page.get(key, [])
            if not isinstance(page_values, list):
                raise RuntimeError(f"iam {arguments[1]} returned invalid {key}")
            values.extend(page_values)
        return {key: values}

    method = getattr(client, operation, None)
    if method is None:
        raise RuntimeError(f"unsupported IAM operation {arguments[1]}")
    result = method(**kwargs)
    if not isinstance(result, dict):
        raise RuntimeError(f"iam {arguments[1]} returned a non-object")
    result.pop("ResponseMetadata", None)
    return result


def _aws_json(arguments: Iterable[str]) -> dict[str, Any]:
    arguments = list(arguments)
    try:
        return _sdk_json(arguments)
    except ImportError:
        pass

    command = ["aws", *arguments, "--output", "json"]
    endpoint = os.environ.get("AWS_ENDPOINT_URL", "").strip()
    if endpoint and "--endpoint-url" not in command:
        command.extend(["--endpoint-url", endpoint])
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        value = json.loads(result.stdout)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        raise RuntimeError(f"{' '.join(command[:3])} failed: {detail.strip()}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"{' '.join(command[:3])} returned a non-object")
    return value


def _matches_any(value: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatchcase(value, pattern) for pattern in patterns)


def role_is_agent_writable(role: dict[str, Any], scope: dict[str, Any]) -> bool:
    arn = str(role.get("Arn", ""))
    path = str(role.get("Path", ""))
    if not arn:
        return False
    # AWS owns this reserved path.  Task builders do not author service-linked
    # roles, even when a coarse builder resource pattern happens to match them.
    if path.startswith("/aws-service-role/"):
        return False
    allowed = _matches_any(arn, _items(scope.get("allow_role_arns")))
    denied = _matches_any(arn, _items(scope.get("deny_role_arns")))
    return allowed and not denied


def inspect(scope: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    roles = _aws_json(["iam", "list-roles"]).get("Roles", [])
    if not isinstance(roles, list):
        raise RuntimeError("iam list-roles returned a non-list Roles field")

    for role in roles:
        if not isinstance(role, dict) or not role_is_agent_writable(role, scope):
            continue
        role_name = str(role.get("RoleName", ""))
        names = _aws_json(
            ["iam", "list-role-policies", "--role-name", role_name]
        ).get("PolicyNames", [])
        if not isinstance(names, list):
            raise RuntimeError(f"iam list-role-policies returned invalid data for {role_name}")
        for policy_name in names:
            result = _aws_json(
                [
                    "iam",
                    "get-role-policy",
                    "--role-name",
                    role_name,
                    "--policy-name",
                    str(policy_name),
                ]
            )
            document = decode_policy_document(result.get("PolicyDocument"))
            if policy_has_universal_allow(document):
                problems.append(f"role {role_name!r}, inline policy {policy_name!r}")

        attached = _aws_json(
            ["iam", "list-attached-role-policies", "--role-name", role_name]
        ).get("AttachedPolicies", [])
        if not isinstance(attached, list):
            raise RuntimeError(
                f"iam list-attached-role-policies returned invalid data for {role_name}"
            )
        for policy in attached:
            if not isinstance(policy, dict):
                raise RuntimeError(f"attached policy entry is invalid for {role_name}")
            policy_name = str(policy.get("PolicyName", ""))
            policy_arn = str(policy.get("PolicyArn", ""))
            if not policy_arn:
                raise RuntimeError(f"attached policy ARN is missing for {role_name}")
            if policy_name.casefold() == "administratoraccess" or policy_arn.casefold().endswith(
                "/administratoraccess"
            ):
                problems.append(
                    f"role {role_name!r}, attached administrative policy {policy_arn!r}"
                )
                continue
            metadata = _aws_json(["iam", "get-policy", "--policy-arn", policy_arn])
            policy_metadata = metadata.get("Policy", {})
            if not isinstance(policy_metadata, dict):
                raise RuntimeError(f"iam get-policy returned invalid data for {policy_arn}")
            version = str(policy_metadata.get("DefaultVersionId", ""))
            if not version:
                raise RuntimeError(f"default policy version is missing for {policy_arn}")
            version_result = _aws_json(
                [
                    "iam",
                    "get-policy-version",
                    "--policy-arn",
                    policy_arn,
                    "--version-id",
                    version,
                ]
            )
            policy_version = version_result.get("PolicyVersion", {})
            if not isinstance(policy_version, dict):
                raise RuntimeError(
                    f"iam get-policy-version returned invalid data for {policy_arn}"
                )
            document = decode_policy_document(policy_version.get("Document"))
            if policy_has_universal_allow(document):
                problems.append(
                    f"role {role_name!r}, universal attached policy {policy_arn!r}"
                )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scope",
        type=Path,
        default=Path(__file__).with_name(CONFIG_NAME),
        help="generated builder-policy scope (default: beside this script)",
    )
    args = parser.parse_args(argv)
    try:
        scope = json.loads(args.scope.read_text())
        problems = inspect(scope)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"[iam-wildcard-role-guard] ERROR: {exc}", file=sys.stderr)
        return 2

    if problems:
        print(
            "[iam-wildcard-role-guard] universal role permissions found on an "
            "agent-writable IAM role:",
            file=sys.stderr,
        )
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print("[iam-wildcard-role-guard] agent-writable inline role policies are scoped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
