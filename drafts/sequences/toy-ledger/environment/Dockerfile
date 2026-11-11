# toy-ledger image (SPEC section 3, image contract).
#
# Build context = this directory. It holds only the base snapshot (repo/);
# hidden tests, gold patches and ticket texts live outside it on purpose.
# python:*-slim-bookworm is a multi-arch manifest (amd64 and arm64).
FROM python:3.12.7-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash ca-certificates git patch procps tar \
    && rm -rf /var/lib/apt/lists/*

# Keep in step with PYTEST_VERSION in ../selfcheck.sh.
RUN pip install "pytest==8.3.3"

# An identity for commits the agent makes. Neutral on purpose: nothing in the
# container should name the experiment (setup.sh commits with its own identity
# and ignores system git config).
RUN git config --system user.name "Developer" \
    && git config --system user.email "developer@localhost" \
    && git config --system init.defaultBranch main \
    && git config --system --add safe.directory /app

COPY repo/ /app/
WORKDIR /app

# 1. Pristine tarball of the base tree (no .git, no caches).
# 2. A git repository with exactly one commit and a fixed author/date, so
#    neither history nor timestamps reveal anything.
RUN set -eu; \
    rm -rf .git; \
    find . \( -name __pycache__ -o -name .pytest_cache \) -prune -exec rm -rf {} +; \
    mkdir -p /opt/odyssey; \
    tar -czf /opt/odyssey/base.tar.gz -C /app .; \
    git init -q; \
    git add -A; \
    GIT_AUTHOR_NAME=Maintainers GIT_AUTHOR_EMAIL=maintainers@localhost \
    GIT_AUTHOR_DATE="2024-01-01T00:00:00+0000" \
    GIT_COMMITTER_NAME=Maintainers GIT_COMMITTER_EMAIL=maintainers@localhost \
    GIT_COMMITTER_DATE="2024-01-01T00:00:00+0000" \
        git commit -q -m "Initial commit"; \
    test "$(git rev-list --count HEAD)" -eq 1

RUN mkdir -p /memory && chmod 0777 /memory
