"""ATL-4 grader: repository-parity check.

The repaired fulfilment code must be the repository's: each deployed function's CodeSha256 must
equal the SHA-256 of the package that deploy/components/fulfillment.py builds from its module in
services/fulfillment/ (one file at the zip's top level, deterministic zip). The packaging rule is
the grader's trusted copy (atlas_grader.package_files); the agent's deploy code is never run.
Code uploaded by hand, or built from anything but the committed module, fails this check.
"""

import os
import sys

import pytest

sys.path.insert(0, os.environ["ATLAS_LIB"])
import atlas_grader as ag  # noqa: E402

FUNCTIONS = {
    "IngestionFunction": "ingestion.py",
    "SplitterFunction": "splitter.py",
    "ProcessorFunction": "processor.py",
}


@pytest.fixture(scope="module")
def lmb():
    return ag.client("lambda")


def test_deployed_code_matches_repository(lmb):
    source = ag.repo() / "services" / "fulfillment"
    mismatches = []
    for function, module in FUNCTIONS.items():
        path = source / module
        assert path.is_file(), f"services/fulfillment/{module} is missing from the repository"
        expected = ag.code_sha256(ag.package_files(path))
        deployed = lmb.get_function_configuration(FunctionName=function)["CodeSha256"]
        if deployed != expected:
            mismatches.append(f"{function}: deployed {deployed}, repository builds {expected}")
    assert not mismatches, (
        "deployed fulfilment code differs from what the repository packages:\n  "
        + "\n  ".join(mismatches)
    )
