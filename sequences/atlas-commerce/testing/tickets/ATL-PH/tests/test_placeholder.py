"""Placeholder grader: the heartbeat queue exists with its release tag."""
import os
import sys

sys.path.insert(0, os.environ["ATLAS_LIB"])
import atlas_grader as ag  # noqa: E402


def test_heartbeat_queue_tagged():
    sqs = ag.client("sqs", verifier=True)
    url = sqs.get_queue_url(QueueName="atlas-ops-heartbeat")["QueueUrl"]
    assert sqs.list_queue_tags(QueueUrl=url).get("Tags", {}).get("release") == "PLACEHOLDER-REF-7f3a9c"
