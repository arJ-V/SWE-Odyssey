# Image contract (docs/SPEC.md section 3): base snapshot at the workdir as a
# one-commit git repo, a pristine tarball of it, and a writable memory dir.
FROM debian:bookworm-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends git patch procps ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY base/ /app/
RUN mkdir -p /opt/odyssey /memory \
 && tar -czf /opt/odyssey/base.tar.gz -C /app . \
 && git init -q \
 && git add -A \
 && git -c user.name=odyssey -c user.email=odyssey@localhost commit -q -m snapshot
