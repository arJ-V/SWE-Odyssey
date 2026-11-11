"""Bedrock's own meter for one model and time window (CloudWatch AWS/Bedrock), to cross-check a run.

    ( eval "$(grep -E '^AWS_(ACCESS_KEY_ID|SECRET_ACCESS_KEY)=' .env | sed 's/^/export /')"
      uv run -q --python 3.12 --with botocore python scripts/open_models/cloudwatch_tokens.py \
          --model minimax.minimax-m2.5 --start 2026-11-11T01:30:00Z --end 2026-11-11T02:00:00Z )

Prints per-minute and total Invocations, InputTokenCount, OutputTokenCount and the cache
counters for the ModelId dimension (the inference-profile id for Kimi). Read-only
(cloudwatch:GetMetricData); prints no credential. CloudWatch counts every call of that
model in the account, so a window must not overlap other users of the same model.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone

import botocore.session

METRICS = ("Invocations", "InputTokenCount", "OutputTokenCount", "CacheReadInputTokenCount",
           "CacheWriteInputTokenCount")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--model", required=True)
    p.add_argument("--start", required=True, help="ISO time, UTC")
    p.add_argument("--end", required=True, help="ISO time, UTC")
    p.add_argument("--region", default="us-east-1")
    p.add_argument("--per-minute", action="store_true")
    args = p.parse_args()
    start = datetime.fromisoformat(args.start.replace("Z", "+00:00"))
    end = datetime.fromisoformat(args.end.replace("Z", "+00:00")) + timedelta(minutes=1)
    cw = botocore.session.get_session().create_client("cloudwatch", region_name=args.region)
    queries = [{"Id": f"m{i}", "Label": name, "ReturnData": True, "MetricStat": {
        "Metric": {"Namespace": "AWS/Bedrock", "MetricName": name,
                   "Dimensions": [{"Name": "ModelId", "Value": args.model}]},
        "Period": 60, "Stat": "Sum"}} for i, name in enumerate(METRICS)]
    out = cw.get_metric_data(MetricDataQueries=queries, StartTime=start, EndTime=end, ScanBy="TimestampAscending")
    totals, minutes = {}, {}
    for series in out["MetricDataResults"]:
        totals[series["Label"]] = int(sum(series["Values"]))
        for ts, value in zip(series["Timestamps"], series["Values"]):
            minutes.setdefault(ts.astimezone(timezone.utc).strftime("%H:%MZ"), {})[series["Label"]] = int(value)
    if args.per_minute:
        for minute in sorted(minutes):
            print(minute, json.dumps(minutes[minute]))
    print(json.dumps({"model": args.model, "start": args.start, "end": args.end, **totals}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
