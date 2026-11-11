# duckdb-2026 image (docs/SPEC.md section 3, image contract).
#
# Base: the SWE-bench-Live/MultiLang evaluation image of duckdb__duckdb-24318
# (Ubuntu 24.04 with gcc 13, clang, cmake 3.28, ninja, python3, git), pinned by
# digest. Its own repository at /testbed (upstream history and a build of a
# later commit) is deleted; the agent's repository is /app.
#
# Stage "source" (thrown away): fetch the snapshot commit S from GitHub (the
# only network access besides the apt install of ccache below), apply
# base.patch (every ticket's fix reverse-applied, its test changes reverted,
# test names scrubbed) and delete the files in base.deleted (the tickets' own
# test files). Only the resulting tree is copied into the final image, so
# neither base.patch nor any git object of S is in a layer the agent can read.
#
# Build cache: a full C++ build of DuckDB takes 15-30 minutes on 4 cores, so
# the image carries a ccache filled by building the BUGGED base once (at /app,
# with `GEN=ninja make release`), after which the build directory is deleted.
# Nothing compiled from fixed code is in the image: the cache only holds
# objects of the base tree. ccache runs with base_dir=/ and hash_dir=false, so
# a build of the same tree at any directory one level below / (the agent's
# /app, the grader's /odyssey-grade-build.*) hits it; only translation units
# whose sources differ from the base recompile.
ARG UPSTREAM_IMAGE=starryzhang/sweb.eval.x86_64.duckdb_1776_duckdb-24318@sha256:01a191813fa2dfa34a4995fb169707504575b7d67123d44dad019b1b264bdab9

FROM ${UPSTREAM_IMAGE} AS source
ARG DUCKDB_REPO=https://github.com/duckdb/duckdb.git
ARG SNAPSHOT=dce07451cb10fdf75b721183333c4a1c08cfe87d
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
WORKDIR /src
RUN git -c init.defaultBranch=main init -q . \
    && git fetch -q --depth 1 "$DUCKDB_REPO" "$SNAPSHOT" \
    && git -c advice.detachedHead=false checkout -q FETCH_HEAD \
    && test "$(git rev-parse HEAD)" = "$SNAPSHOT" \
    && test -z "$(git submodule status)"
COPY base.patch base.deleted /tmp/stack/
RUN git apply --index --whitespace=nowarn /tmp/stack/base.patch \
    && xargs -r -d '\n' git rm -q -- < /tmp/stack/base.deleted \
    && mkdir /base \
    && git ls-files -z | tar --null -T - -cf - | tar -xf - -C /base

FROM ${UPSTREAM_IMAGE}
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# The upstream benchmark repository (history up to a later commit, i.e. the
# fixes) and its build outputs go, and so does the upstream build log.
RUN rm -rf /testbed /tmp/duckdb-build.log

RUN apt-get update -qq \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends ccache \
    && rm -rf /var/lib/apt/lists/* \
    && ccache --version | head -n 1

# One cache for everyone in the container (the agent's builds and the grader's).
RUN printf '%s\n' 'cache_dir = /var/cache/ccache' 'base_dir = /' 'hash_dir = false' \
        'max_size = 20G' 'compression = true' > /etc/ccache.conf \
    && mkdir -p /var/cache/ccache && chmod 0777 /var/cache/ccache

# A neutral identity for commits the agent makes; nothing names the experiment.
RUN git config --system user.name "Developer" \
    && git config --system user.email "developer@localhost" \
    && git config --system init.defaultBranch main \
    && git config --system --add safe.directory /app

COPY --from=source /base/ /app/
WORKDIR /app

# 1. Pristine tarball of the base tree (no .git).
# 2. A git repository with exactly one commit and a fixed author and date.
RUN set -eu; \
    mkdir -p /opt/odyssey; \
    tar -czf /opt/odyssey/base.tar.gz -C /app .; \
    git init -q; \
    git add -A; \
    GIT_AUTHOR_NAME=Maintainers GIT_AUTHOR_EMAIL=maintainers@localhost \
    GIT_AUTHOR_DATE="2024-01-01T00:00:00+0000" \
    GIT_COMMITTER_NAME=Maintainers GIT_COMMITTER_EMAIL=maintainers@localhost \
    GIT_COMMITTER_DATE="2024-01-01T00:00:00+0000" \
        git commit -q -m "Initial commit"; \
    test "$(git rev-list --count HEAD)" -eq 1; \
    test -z "$(git status --porcelain)"

# Fill the ccache from the bugged base, then drop every build output. The
# tree must come out exactly as it went in.
RUN set -eu; \
    ccache -z >/dev/null; \
    GEN=ninja make release > /tmp/cache-build.log 2>&1 || { tail -n 80 /tmp/cache-build.log; exit 1; }; \
    ccache -s | head -n 12; \
    rm -rf /app/build /tmp/cache-build.log; \
    git clean -ffdxq; \
    test -z "$(git status --porcelain --ignored)"; \
    du -sh /var/cache/ccache

# The repository's AGENTS.md recommends `make reldebug`; cache that
# configuration too (same procedure), so following it does not cost a cold
# build. The grader only uses the release configuration above.
RUN set -eu; \
    ccache -z >/dev/null; \
    make reldebug > /tmp/cache-build.log 2>&1 || { tail -n 80 /tmp/cache-build.log; exit 1; }; \
    ccache -s | head -n 12; \
    rm -rf /app/build /tmp/cache-build.log; \
    git clean -ffdxq; \
    test -z "$(git status --porcelain --ignored)"; \
    du -sh /var/cache/ccache

# The grader builds a copy of the tree at /check with the same CMake command.
# The test runner's sources embed their source and build paths, so fill the
# cache for that path too (only those translation units miss here).
RUN set -eu; \
    mkdir /check; \
    (cd /app && tar --exclude=./.git --exclude=./build -cf - .) | (cd /check && tar -xf -); \
    mkdir -p /check/build/release; \
    cd /check/build/release; \
    ccache -z >/dev/null; \
    { cmake -G "Ninja" -DFORCE_COLORED_OUTPUT=1 -DLOCAL_EXTENSION_REPO="" -DOVERRIDE_GIT_DESCRIBE="" \
        -DDUCKDB_EXPLICIT_VERSION="" -DCMAKE_BUILD_TYPE=Release ../.. && ninja unittest; } \
        > /tmp/cache-build.log 2>&1 || { tail -n 80 /tmp/cache-build.log; exit 1; }; \
    ccache -s | head -n 6; \
    cd /; rm -rf /check /tmp/cache-build.log

# `git clean -x` above also deleted test data the upstream repository tracks
# although its .gitignore matches it (data/storage/*.db, ...; the one-commit
# repository here does not track ignored files). Put the base tree back from
# the tarball and prove /app is exactly the base again.
RUN set -eu; \
    tar -xzf /opt/odyssey/base.tar.gz -C /app; \
    d="$(mktemp -d)"; tar -xzf /opt/odyssey/base.tar.gz -C "$d"; \
    diff -rq --no-dereference --exclude=.git "$d" /app; \
    rm -rf "$d"; \
    test -z "$(git status --porcelain)"

# The upstream image's /root/.gitconfig sets an identity ("haha
# <example@gmail.com>") that would override the neutral system identity, and
# trusts /testbed; it goes.
RUN rm -f /root/.gitconfig && test "$(git config --global --list 2>/dev/null | wc -l)" -eq 0

RUN mkdir -p /memory && chmod 0777 /memory
