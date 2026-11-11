# atlas-commerce images, built by Harbor from this context (see docker-compose.yaml).
#   target `agent`       the main container: the workstation the agent uses, the repository at /app
#   target `controller`  the trusted reset controller (no network; Docker socket)
# The build needs network (apt, the AWS CLI, PyPI, npm, the Docker CLI); nothing that runs in a
# trial downloads anything. Everything a grader needs is installed here, in /opt/atlas-grader.

FROM public.ecr.aws/ubuntu/ubuntu:24.04@sha256:c7961894220d8080d1cfe3e885299049fe90eed4715f78ff9a9a784e5d800359 AS toolchain

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl unzip zip jq less git patch python3 python3-pip python3-venv nodejs npm \
    && rm -rf /var/lib/apt/lists/*

ARG AWSCLI_VERSION=2.27.50
RUN case "$(uname -m)" in x86_64) a=x86_64;; aarch64) a=aarch64;; *) echo "unsupported arch" >&2; exit 1;; esac \
    && curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-${a}-${AWSCLI_VERSION}.zip" -o /tmp/awscliv2.zip \
    && unzip -q /tmp/awscliv2.zip -d /tmp && /tmp/aws/install && rm -rf /tmp/aws /tmp/awscliv2.zip

# The workstation's Python toolchain (boto3/botocore, awscli-local, requests, pyyaml and uv).
RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir \
      boto3==1.35.99 botocore==1.35.99 awscli-local==0.22.0 requests==2.32.3 pyyaml==6.0.2 uv==0.9.7

# Graders' toolchain, so no test.sh downloads anything at grade time.
RUN python3 -m venv /opt/atlas-grader && /opt/atlas-grader/bin/pip install --no-cache-dir \
      pytest==8.4.1 pytest-json-ctrf==0.3.5 boto3==1.35.99 botocore==1.35.99 requests==2.32.3 pyyaml==6.0.2

# IAM policy gate toolchain of ATL-6's iam_gate.py (node >= 18, pinned engine).
RUN node -e 'process.exit(parseInt(process.versions.node.split(".")[0],10) >= 18 ? 0 : 1)' \
    && mkdir -p /opt/iam-gate \
    && npm install --prefix /opt/iam-gate --silent \
         @cloud-copilot/iam-simulate@0.1.154 @cloud-copilot/iam-data@0.19.202606021 \
    && ln -s /opt/iam-gate/node_modules /node_modules

# --- the trusted controller ------------------------------------------------------------------
FROM toolchain AS controller
ARG DOCKER_CLI_VERSION=27.5.1
RUN case "$(uname -m)" in x86_64) a=x86_64;; aarch64) a=aarch64;; *) exit 1;; esac \
    && curl -fsSL "https://download.docker.com/linux/static/stable/${a}/docker-${DOCKER_CLI_VERSION}.tgz" \
       | tar -xz -C /usr/local/bin --strip-components=1 docker/docker \
    && docker --version
COPY controller/controller.py /opt/atlas-controller/controller.py
CMD ["python3", "-u", "/opt/atlas-controller/controller.py"]

# --- the agent's workstation (Harbor's `main`) ----------------------------------------------------
FROM toolchain AS agent
ENV PATH="/opt/venv/bin:${PATH}" \
    AWS_DEFAULT_REGION=us-east-1 \
    AWS_ENDPOINT_URL=http://localstack:4566 \
    AWS_SHARED_CREDENTIALS_FILE=/shared/agent/credentials \
    AWS_PAGER=""
RUN mkdir -p /root/.aws && printf '[default]\nregion = us-east-1\noutput = json\n' > /root/.aws/config \
    && git config --system user.name "Atlas Developer" \
    && git config --system user.email "developer@atlas-commerce.invalid" \
    && git config --system init.defaultBranch main
COPY platform/guard.py /opt/atlas/guard.py
# The repository: the base snapshot, as a git repository with one commit, and its pristine
# copy for the step scripts (SPEC section 3, image contract).
COPY repo/ /app/
RUN mkdir -p /opt/odyssey /memory \
    && tar -czf /opt/odyssey/base.tar.gz -C /app . \
    && cd /app \
    && GIT_AUTHOR_DATE='2024-01-01T00:00:00+0000' GIT_COMMITTER_DATE='2024-01-01T00:00:00+0000' \
       GIT_AUTHOR_NAME=Maintainers GIT_AUTHOR_EMAIL=maintainers@localhost \
       GIT_COMMITTER_NAME=Maintainers GIT_COMMITTER_EMAIL=maintainers@localhost \
       sh -c 'git init -q && git add -A && git commit -q -m "Initial commit"'
WORKDIR /app
CMD ["python3", "-I", "/opt/atlas/guard.py"]
