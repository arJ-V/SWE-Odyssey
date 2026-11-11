"""Pure IAM capability-envelope checks for the pipeline execution role.

The live verifier supplies policy documents read from IAM.  These helpers keep
statement layout out of the contract: actions are matched with IAM's
case-insensitive glob semantics, and resources for other services (or
incompatible resource types) do not become part of a service grant merely
because they share a statement.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Iterable


def _action_resource_catalogue(service: str, rows: str) -> dict[str, frozenset[str]]:
    catalogue = {}
    for row in rows.splitlines():
        action, resource_types = row.split(maxsplit=1)
        catalogue[f"{service}:{action}"] = frozenset(
            () if resource_types == "-" else resource_types.split(",")
        )
    return catalogue


# Snapshot of the AWS Service Authorization Reference v1.4, not the SDK API
# model.  The authorization reference includes permission-only actions and the
# resource types on which every IAM action is effective.
KMS_ACTION_RESOURCES = _action_resource_catalogue(
    "kms",
    """CancelKeyDeletion key
ConnectCustomKeyStore -
CreateAlias alias,key
CreateCustomKeyStore -
CreateGrant key
CreateKey -
Decrypt key
DeleteAlias alias,key
DeleteCustomKeyStore -
DeleteImportedKeyMaterial key
DeriveSharedSecret key
DescribeCustomKeyStores -
DescribeKey key
DisableKey key
DisableKeyRotation key
DisconnectCustomKeyStore -
EnableKey key
EnableKeyRotation key
Encrypt key
GenerateDataKey key
GenerateDataKeyPair key
GenerateDataKeyPairWithoutPlaintext key
GenerateDataKeyWithoutPlaintext key
GenerateMac key
GenerateRandom -
GetKeyLastUsage key
GetKeyPolicy key
GetKeyRotationStatus key
GetParametersForImport key
GetPublicKey key
ImportKeyMaterial key
ListAliases -
ListGrants key
ListKeyPolicies key
ListKeyRotations key
ListKeys -
ListResourceTags key
ListRetirableGrants -
PutKeyPolicy key
ReEncryptFrom key
ReEncryptTo key
ReplicateKey key
RetireGrant key
RevokeGrant key
RotateKeyOnDemand key
ScheduleKeyDeletion key
Sign key
SynchronizeMultiRegionKey key
TagResource key
UntagResource key
UpdateAlias alias,key
UpdateCustomKeyStore -
UpdateKeyDescription key
UpdatePrimaryRegion key
Verify key
VerifyMac key""",
)

DYNAMODB_ACTION_RESOURCES = _action_resource_catalogue(
    "dynamodb",
    """AssociateTableReplica table
BatchGetItem table
BatchWriteItem table
ConditionCheckItem table
CreateBackup table
CreateGlobalTable global-table,table
CreateGlobalTableWitness table
CreateTable table
CreateTableReplica table
DeleteBackup backup
DeleteGlobalTableWitness table
DeleteItem table
DeleteResourcePolicy stream,table
DeleteTable table
DeleteTableReplica table
DescribeBackup backup
DescribeContinuousBackups table
DescribeContributorInsights index,table
DescribeEndpoints -
DescribeExport export
DescribeGlobalTable global-table
DescribeGlobalTableSettings global-table
DescribeImport import
DescribeKinesisStreamingDestination table
DescribeLimits -
DescribeReservedCapacity -
DescribeReservedCapacityOfferings -
DescribeStream stream
DescribeTable table
DescribeTableReplicaAutoScaling table
DescribeTimeToLive table
DisableKinesisStreamingDestination table
EnableKinesisStreamingDestination table
ExportTableToPointInTime table
GetAbacStatus -
GetItem table
GetRecords stream
GetResourcePolicy stream,table
GetShardIterator stream
ImportTable table
InjectError -
ListBackups -
ListContributorInsights -
ListExports table
ListGlobalTables -
ListImports table
ListStreams -
ListTables -
ListTagsOfResource stream,table
PartiQLDelete table
PartiQLInsert table
PartiQLSelect index,table
PartiQLUpdate table
PurchaseReservedCapacityOfferings -
PutItem table
PutResourcePolicy stream,table
Query index,table
ReadDataForReplication table
ReplicateSettings table
RestoreTableFromAwsBackup table
RestoreTableFromBackup backup,table
RestoreTableToPointInTime table
Scan index,table
SearchVectors index,table
StartAwsBackupJob table
TagResource stream,table
UntagResource stream,table
UpdateAbacStatus -
UpdateContinuousBackups table
UpdateContributorInsights index,table
UpdateGlobalTable global-table,table
UpdateGlobalTableSettings global-table,table
UpdateGlobalTableVersion global-table,table
UpdateItem table
UpdateKinesisStreamingDestination table
UpdateTable table
UpdateTableReplicaAutoScaling table
UpdateTimeToLive table
WriteDataForReplication table""",
)

KMS_ACTIONS = frozenset(KMS_ACTION_RESOURCES)
DYNAMODB_ACTIONS = frozenset(DYNAMODB_ACTION_RESOURCES)

KMS_ALLOWED_ACTIONS = frozenset(
    {
        "kms:Decrypt",
        "kms:DescribeKey",
        "kms:GenerateDataKey",
        "kms:GenerateDataKeyPair",
        "kms:GenerateDataKeyPairWithoutPlaintext",
        "kms:GenerateDataKeyWithoutPlaintext",
    }
)


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _iam_glob_matches(value: str, pattern: str, *, case_sensitive: bool) -> bool:
    """Match IAM's two wildcard characters (``*`` and ``?``)."""

    expression = re.escape(pattern).replace(r"\*", ".*").replace(r"\?", ".")
    flags = 0 if case_sensitive else re.IGNORECASE
    return re.fullmatch(expression, value, flags=flags) is not None


def _statements(policy_docs: Iterable[dict[str, Any]]) -> Iterable[dict[str, Any]]:
    for document in policy_docs:
        statements = document.get("Statement", [])
        for statement in _as_list(statements):
            if isinstance(statement, dict):
                yield statement


def _action_matches_statement(statement: dict[str, Any], action: str) -> bool:
    """Evaluate Action/NotAction for one concrete IAM action."""

    if "Action" in statement:
        return any(
            _iam_glob_matches(action, str(pattern), case_sensitive=False)
            for pattern in _as_list(statement["Action"])
        )
    if "NotAction" in statement:
        return not any(
            _iam_glob_matches(action, str(pattern), case_sensitive=False)
            for pattern in _as_list(statement["NotAction"])
        )
    return False


def _resource_matches_statement(
    statement: dict[str, Any], resource: str
) -> bool:
    """Evaluate Resource/NotResource for one concrete IAM resource."""

    if "Resource" in statement:
        return any(
            _iam_glob_matches(resource, str(pattern), case_sensitive=True)
            for pattern in _as_list(statement["Resource"])
        )
    if "NotResource" in statement:
        return not any(
            _iam_glob_matches(resource, str(pattern), case_sensitive=True)
            for pattern in _as_list(statement["NotResource"])
        )
    return False


def _pattern_covers(outer: str, inner: str) -> bool:
    """Conservatively prove that one IAM resource glob contains another."""

    if outer == "*" or outer == inner:
        return True
    if "*" not in inner and "?" not in inner:
        return _iam_glob_matches(inner, outer, case_sensitive=True)
    return False


def _statement_problem(
    statement: dict[str, Any],
    action_catalogue: frozenset[str],
    service: str,
    resource_kind: Callable[[str], str | None],
) -> str | None:
    """Reject service-relevant policy syntax we cannot evaluate exactly."""

    actions = _expanded_actions(statement, action_catalogue, service)
    if not actions:
        return None
    if "Action" in statement and "NotAction" in statement:
        return f"unsupported {service} statement with Action and NotAction"
    if "Resource" in statement and "NotResource" in statement:
        return f"unsupported {service} statement with Resource and NotResource"
    if "Resource" not in statement and "NotResource" not in statement:
        return f"unsupported {service} statement without a resource selector"
    if "Resource" in statement and not any(
        resource_kind(str(value)) is not None
        for value in _as_list(statement["Resource"])
    ):
        return None
    if "NotResource" in statement and any(
        str(value) == "*" for value in _as_list(statement["NotResource"])
    ):
        return None
    if "Condition" in statement:
        return f"unsupported conditional {service} statement"
    effect = str(statement.get("Effect", "")).lower()
    if effect not in {"allow", "deny"}:
        return f"unsupported {service} statement effect {effect!r}"
    return None


def _expanded_actions(
    statement: dict[str, Any], action_catalogue: frozenset[str], service: str
) -> set[str]:
    """Return IAM actions selected by Action or NotAction.

    An unmatched pattern that targets this service is retained as an unknown
    action.  This makes newly introduced permission-only actions fail closed
    instead of disappearing merely because the local catalogue predates them.
    """

    if "Action" in statement:
        patterns = [str(value) for value in _as_list(statement["Action"])]
        selected = {
            action
            for action in action_catalogue
            if any(
                _iam_glob_matches(action, pattern, case_sensitive=False)
                for pattern in patterns
            )
        }
        for pattern in patterns:
            matched = any(
                _iam_glob_matches(action, pattern, case_sensitive=False)
                for action in action_catalogue
            )
            service_pattern = pattern.split(":", 1)[0]
            if (
                not matched
                and ":" in pattern
                and _iam_glob_matches(service, service_pattern, case_sensitive=False)
            ):
                selected.add(f"{service}:{pattern.split(':', 1)[1]}")
        return selected
    if "NotAction" in statement:
        patterns = [str(value) for value in _as_list(statement["NotAction"])]
        return {
            action
            for action in action_catalogue
            if not any(
                _iam_glob_matches(action, pattern, case_sensitive=False)
                for pattern in patterns
            )
        }
    return set()


def _resource_kind(resource: str, expected: str, service: str) -> str | None:
    """Classify a service ARN pattern by the resource type it can address."""
    if resource == "*":
        return "wildcard"
    parts = resource.split(":", 5)
    if len(parts) != 6 or parts[0].lower() != "arn":
        return (
            "wildcard"
            if _iam_glob_matches(expected, resource, case_sensitive=True)
            else None
        )
    service_pattern = parts[2].lower()
    if not _iam_glob_matches(service, service_pattern, case_sensitive=False):
        return None

    resource_part = parts[5].lower()
    type_pattern = resource_part.split("/", 1)[0].lower()
    known_types = {"key", "alias"} if service == "kms" else {"table", "global-table"}
    matching_types = {
        kind
        for kind in known_types
        if _iam_glob_matches(kind, type_pattern, case_sensitive=False)
    }
    if not matching_types:
        return None
    if len(matching_types) > 1:
        return "wildcard"

    kind = matching_types.pop()
    if service == "dynamodb" and kind == "table":
        # DynamoDB encodes these resource types beneath table/<name>/ rather
        # than in the first ARN resource segment.
        for nested_kind in ("index", "stream", "backup", "export", "import"):
            if f"/{nested_kind}/" in resource_part:
                return nested_kind
        # In a Resource ARN, a wildcard in the final segment can span '/'.
        # table/* and table/name/*/* therefore address nested resource kinds,
        # even though their first literal ARN segment says table.
        if "*" in resource_part or "?" in resource_part:
            return "wildcard"
    return kind


def _action_applies(
    action: str, kind: str, catalogue: dict[str, frozenset[str]]
) -> bool:
    """Whether this action/resource-kind pair can produce an IAM grant."""

    if kind == "wildcard":
        return True
    supported = catalogue.get(action)
    if supported is None:
        # Unknown service-prefixed actions fail closed on any recognized ARN.
        return True
    return kind in supported


def _evaluate_envelope(
    policy_docs: Iterable[dict[str, Any]],
    *,
    service: str,
    action_catalogue: frozenset[str],
    allowed_actions: frozenset[str],
    required_actions: frozenset[str],
    expected_resource: str,
    resource_kind: Callable[[str], str | None],
    action_applies: Callable[[str, str], bool],
) -> list[str]:
    statements = list(_statements(policy_docs))
    granted: set[str] = set()
    excess_actions: set[str] = set()
    excess_resources: set[str] = set()
    unsupported: list[str] = []

    usable_statements = []
    for statement in statements:
        problem = _statement_problem(
            statement, action_catalogue, service, resource_kind
        )
        if problem:
            unsupported.append(problem)
            continue
        usable_statements.append(statement)

    allows = [
        statement
        for statement in usable_statements
        if str(statement.get("Effect", "")).lower() == "allow"
    ]
    denies = [
        statement
        for statement in usable_statements
        if str(statement.get("Effect", "")).lower() == "deny"
    ]

    def denied(action: str, resource: str) -> bool:
        return any(
            _action_matches_statement(statement, action)
            and _resource_matches_statement(statement, resource)
            for statement in denies
        )

    def deny_covers_domain(
        action: str, resource_pattern: str, complement: bool
    ) -> bool:
        """Prove a Deny removes an entire resource domain from one Allow."""

        for statement in denies:
            if not _action_matches_statement(statement, action):
                continue
            if "Resource" in statement:
                deny_patterns = [
                    str(value) for value in _as_list(statement["Resource"])
                ]
                if complement:
                    if any(pattern == "*" for pattern in deny_patterns):
                        return True
                elif any(
                    _pattern_covers(pattern, resource_pattern)
                    for pattern in deny_patterns
                ):
                    return True
            elif "NotResource" in statement and not complement:
                exceptions = [
                    str(value) for value in _as_list(statement["NotResource"])
                ]
                # For an exact Allow resource, a NotResource Deny covers it
                # precisely when it is outside every exception.
                if "*" not in resource_pattern and "?" not in resource_pattern:
                    if not any(
                        _iam_glob_matches(
                            resource_pattern, pattern, case_sensitive=True
                        )
                        for pattern in exceptions
                    ):
                        return True
        return False

    def domain_restricted_to_expected(
        action: str, resource_pattern: str, complement: bool
    ) -> bool:
        """Prove Deny/NotResource reduces an Allow domain to the exact ARN."""

        if not complement and resource_pattern == expected_resource:
            return True
        if not complement and not _iam_glob_matches(
            expected_resource, resource_pattern, case_sensitive=True
        ):
            return False
        if complement and any(
            _iam_glob_matches(expected_resource, pattern, case_sensitive=True)
            for pattern in resource_pattern.split("\0")
            if pattern
        ):
            return False

        for statement in denies:
            if not _action_matches_statement(statement, action):
                continue
            if "NotResource" not in statement:
                continue
            exceptions = [
                str(value) for value in _as_list(statement["NotResource"])
            ]
            # This is the common, future-proof IAM boundary: deny every
            # resource except the one published ARN.  Do not attempt general
            # glob-language subtraction; unsupported shapes remain closed.
            if exceptions == [expected_resource]:
                return True
        return False

    def allow_domains(
        statement: dict[str, Any]
    ) -> list[tuple[str, str, bool]]:
        """Return (pattern, kind, is_complement) resource domains."""

        if "Resource" in statement:
            domains = []
            for value in _as_list(statement["Resource"]):
                pattern = str(value)
                kind = resource_kind(pattern)
                if kind is not None:
                    domains.append((pattern, kind, False))
            return domains

        exclusions = [str(value) for value in _as_list(statement["NotResource"])]
        if any(value == "*" for value in exclusions):
            return []
        # An Allow/NotResource denotes an open set of service resources.  Keep
        # its exclusions so required-resource membership can be checked, but
        # classify the domain as wildcard for upper-bound evaluation.
        return [("\0".join(exclusions), "wildcard", True)]

    for statement in allows:
        actions = _expanded_actions(statement, action_catalogue, service)
        if not actions:
            continue

        for action in actions:
            for resource, kind, complement in allow_domains(statement):
                if not action_applies(action, kind):
                    continue
                if deny_covers_domain(action, resource, complement):
                    continue
                if action not in allowed_actions:
                    excess_actions.add(action)
                    continue
                if not domain_restricted_to_expected(
                    action, resource, complement
                ):
                    excess_resources.add(
                        "NotResource" if complement else resource
                    )

        for action in required_actions:
            if not _action_matches_statement(statement, action):
                continue
            if not _resource_matches_statement(statement, expected_resource):
                continue
            if not denied(action, expected_resource):
                granted.add(action)

    problems = list(dict.fromkeys(unsupported))
    missing = required_actions - granted
    if missing:
        problems.append(f"missing required actions: {sorted(missing)}")
    if excess_actions:
        problems.append(
            f"actions outside the allowed envelope: {sorted(excess_actions)}"
        )
    if excess_resources:
        problems.append(
            "resources outside the exact required ARN: "
            f"{sorted(excess_resources)}"
        )
    return problems


def kms_capability_problems(
    policy_docs: Iterable[dict[str, Any]], key_arn: str
) -> list[str]:
    """Validate Decrypt + GenerateDataKey, with the documented KMS envelope."""

    return _evaluate_envelope(
        policy_docs,
        service="kms",
        action_catalogue=KMS_ACTIONS,
        allowed_actions=KMS_ALLOWED_ACTIONS,
        required_actions=frozenset({"kms:Decrypt", "kms:GenerateDataKey"}),
        expected_resource=key_arn,
        resource_kind=lambda resource: _resource_kind(resource, key_arn, "kms"),
        action_applies=lambda action, kind: _action_applies(
            action, kind, KMS_ACTION_RESOURCES
        ),
    )


def dynamodb_capability_problems(
    policy_docs: Iterable[dict[str, Any]], table_arn: str
) -> list[str]:
    """Validate that PutItem on one exact table is the full DynamoDB grant."""

    only_put_item = frozenset({"dynamodb:PutItem"})
    return _evaluate_envelope(
        policy_docs,
        service="dynamodb",
        action_catalogue=DYNAMODB_ACTIONS,
        allowed_actions=only_put_item,
        required_actions=only_put_item,
        expected_resource=table_arn,
        resource_kind=lambda resource: _resource_kind(
            resource, table_arn, "dynamodb"
        ),
        action_applies=lambda action, kind: _action_applies(
            action, kind, DYNAMODB_ACTION_RESOURCES
        ),
    )
