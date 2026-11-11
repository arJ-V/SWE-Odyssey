"""Mint a short-lived, Bedrock-only bearer token for an agent under test.

Two layers, so the token the agent can read is worth little:

1. STS GetFederationToken with an inline session policy: the session may only call
   bedrock:InvokeModel / InvokeModelWithResponseStream on the named models (their
   foundation-model ARNs in any region and their us./global. inference profiles), plus
   bedrock:CallWithBearerToken. Effective permissions are the intersection of that
   policy and the IAM user's own, so nothing else (no S3, EC2, IAM, other models).
2. A Bedrock short-term API key (bearer token) presigned with those session
   credentials, valid for --minutes (and never longer than the session).

The token goes to stdout and nowhere else; pipe it straight into the process that
needs it (scripts/open_models/box_run.sh reads it from stdin on the box). Nothing
is written to disk. The IAM user's long-lived key is read from the environment of
this process only (load it in a subshell from .env, AWS_* lines only).

    ( eval "$(grep -E '^AWS_(ACCESS_KEY_ID|SECRET_ACCESS_KEY)=' .env | sed 's/^/export /')"
      uv run --python 3.12 --with botocore python scripts/open_models/bedrock_token.py \
          --model moonshotai.kimi-k3 --minutes 90 ) | ssh <box> '...'

--check makes one tiny call with the token to the OpenAI-compatible endpoint and prints
only the HTTP status and token usage to stderr (the token itself is never printed).
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import urllib.request

import botocore.session
from botocore.auth import SigV4QueryAuth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials

TOKEN_PREFIX = "bedrock-api-key-"


def session_policy(models: list[str], account: str) -> dict:
    resources = []
    for model in models:
        model = re.sub(r"^(?:us|global)\.", "", model)  # an inference-profile id names the same model
        resources.append(f"arn:aws:bedrock:*::foundation-model/{model}")
        resources.append(f"arn:aws:bedrock:*:{account}:inference-profile/us.{model}")
        resources.append(f"arn:aws:bedrock:*:{account}:inference-profile/global.{model}")
    return {
        "Version": "2012-10-17",
        "Statement": [
            {"Effect": "Allow",
             "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
             "Resource": resources},
            {"Effect": "Allow", "Action": ["bedrock:CallWithBearerToken"], "Resource": "*"},
        ],
    }


def federation_credentials(models: list[str], minutes: int, region: str) -> Credentials:
    session = botocore.session.get_session()
    sts = session.create_client("sts", region_name=region)
    account = sts.get_caller_identity()["Account"]
    policy = json.dumps(session_policy(models, account), separators=(",", ":"))
    out = sts.get_federation_token(
        Name="odyssey-open-models", DurationSeconds=max(900, minutes * 60), Policy=policy)
    c = out["Credentials"]
    print(f"session expires {c['Expiration'].isoformat()}", file=sys.stderr)
    return Credentials(c["AccessKeyId"], c["SecretAccessKey"], c["SessionToken"])


def bearer_token(creds: Credentials, region: str, minutes: int) -> str:
    """Bedrock short-term API key: a SigV4-presigned CallWithBearerToken request, base64'd."""
    request = AWSRequest(method="POST", url="https://bedrock.amazonaws.com/",
                         headers={"host": "bedrock.amazonaws.com"},
                         params={"Action": "CallWithBearerToken"})
    SigV4QueryAuth(creds, "bedrock", region, expires=minutes * 60).add_auth(request)
    url = request.url.replace("https://", "", 1) + "&Version=1"
    return TOKEN_PREFIX + base64.b64encode(url.encode()).decode()


def check(token: str, base_url: str, model: str) -> None:
    body = json.dumps({"model": model, "max_tokens": 16,
                       "messages": [{"role": "user", "content": "Reply with the single word OK."}]}).encode()
    req = urllib.request.Request(base_url.rstrip("/") + "/chat/completions", data=body, method="POST",
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
            print(f"check {model}: HTTP {resp.status} usage={json.dumps(data.get('usage'))}", file=sys.stderr)
    except urllib.error.HTTPError as exc:
        print(f"check {model}: HTTP {exc.code} {exc.read()[:300]!r}", file=sys.stderr)
    except OSError as exc:  # timeouts, resets
        print(f"check {model}: {type(exc).__name__} {exc}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--model", action="append", required=True,
                   help="Bedrock model or inference-profile id (repeatable), e.g. qwen.qwen3-coder-next, us.moonshotai.kimi-k3")
    p.add_argument("--minutes", type=int, default=90, help="token and session lifetime (15-720)")
    p.add_argument("--region", default="us-east-1")
    p.add_argument("--check", metavar="BASE_URL",
                   help="make one tiny chat call per --model with the token (stderr only), print no token")
    args = p.parse_args(argv)
    if not 15 <= args.minutes <= 720:
        p.error("--minutes must be 15..720")
    creds = federation_credentials(args.model, args.minutes, args.region)
    token = bearer_token(creds, args.region, args.minutes)
    if args.check:
        for model in args.model:
            check(token, args.check, model)
        return 0
    sys.stdout.write(token + "\n")  # newline-terminated: `read` on the far side needs it
    return 0


if __name__ == "__main__":
    sys.exit(main())
