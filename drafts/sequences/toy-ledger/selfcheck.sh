#!/usr/bin/env bash
# Self-check for the toy-ledger sequence. Needs uv and git; no Docker.
#
# Verifies, on throwaway copies of environment/repo:
#   * the visible test-suite passes at base and at base + every gold patch;
#   * for every ticket, the four ticket-contract conditions of SPEC section 3
#     (run.sh fails at base, passes with its own gold, passes with all gold,
#     fails with every gold except its own) and that details.json agrees;
#   * every gold patch applies in every order (all permutations) and every
#     order yields the same tree;
#   * the Docker build context holds no hidden tests, gold patches or memory files.
#
# Usage: drafts/sequences/toy-ledger/selfcheck.sh [-v]    (-v prints grader output)
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$HERE/environment/repo"
PYTEST_VERSION="8.3.3"   # keep in step with environment/Dockerfile
VERBOSE=0
[ "${1:-}" = "-v" ] && VERBOSE=1

tmp="$(mktemp -d "${TMPDIR:-/tmp}/toy-ledger-selfcheck.XXXXXX")"
trap 'rm -rf "$tmp"' EXIT
failures=0

ok() { printf '  ok    %s\n' "$1"; }
bad() { printf '  FAIL  %s\n' "$1"; failures=$((failures + 1)); }

# --- environment ---------------------------------------------------------

echo "Setting up Python 3.12 + pytest $PYTEST_VERSION in a temporary venv"
uv venv -q --python 3.12 "$tmp/venv"
uv pip install -q --python "$tmp/venv/bin/python" "pytest==$PYTEST_VERSION"
PY="$tmp/venv/bin/python"
export ODYSSEY_PYTHON="$PY"

# Ticket ids come from sequence.toml, the same place the compiler reads them.
TICKETS=()
while IFS= read -r id; do TICKETS+=("$id"); done < <(
    "$PY" -c 'import sys, tomllib; [print(t["id"]) for t in tomllib.load(open(sys.argv[1], "rb"))["tickets"]]' \
        "$HERE/sequence.toml"
)
echo "Tickets: ${TICKETS[*]}"

# --- helpers -------------------------------------------------------------

fresh_tree() {
    # fresh_tree <dest>: a clean copy of the base snapshot (no caches).
    rm -rf "$1"
    mkdir -p "$1"
    (cd "$REPO" && tar --exclude __pycache__ --exclude .pytest_cache -cf - .) | (cd "$1" && tar -xf -)
}

apply_gold() {
    # apply_gold <dest> <ticket...>: apply those tickets' gold patches.
    local dest="$1" id
    shift
    for id in "$@"; do
        (cd "$dest" && git apply "$HERE/tickets/$id/gold.patch")
    done
}

grade() {
    # grade <dest> <ticket>: overlay hidden files, run run.sh; returns its status.
    local dest="$1" id="$2" status=0
    cp -R "$HERE/tickets/$id/tests/files/." "$dest/"
    (cd "$dest" && ODYSSEY_OUT="$dest.out" bash "$HERE/tickets/$id/tests/run.sh") \
        >"$dest.log" 2>&1 || status=$?
    [ "$VERBOSE" -eq 1 ] && cat "$dest.log"
    return "$status"
}

details_resolved() {
    # details_resolved <dest>: print the "resolved" flag from details.json.
    "$PY" -c 'import json, sys; print(str(json.load(open(sys.argv[1]))["resolved"]).lower())' \
        "$1.out/details.json"
}

expect() {
    # expect <pass|fail> <label> <dest> <ticket...gold to apply>
    local want="$1" label="$2" dest="$3" ticket="$4" status=0
    shift 4
    fresh_tree "$dest"
    apply_gold "$dest" "$@"
    grade "$dest" "$ticket" || status=$?
    local got=fail
    [ "$status" -eq 0 ] && got=pass
    local flag
    flag="$(details_resolved "$dest" 2>/dev/null || echo missing)"
    if [ "$got" = "$want" ] && [ "$flag" = "$([ "$want" = pass ] && echo true || echo false)" ]; then
        ok "$label: $got"
    else
        bad "$label: expected $want, got $got (details.json resolved=$flag)"
        tail -n 25 "$dest.log" | sed 's/^/        /'
    fi
}

visible_suite() {
    # visible_suite <label> <ticket...gold to apply>
    local label="$1" dest="$tmp/visible"
    shift
    fresh_tree "$dest"
    apply_gold "$dest" "$@"
    # Uses the repo's own pytest.ini, exactly as a developer would run it.
    if (cd "$dest" && "$PY" -m pytest) >"$dest.log" 2>&1; then
        ok "visible suite at $label: $(tail -n 1 "$dest.log")"
    else
        bad "visible suite at $label"
        tail -n 25 "$dest.log" | sed 's/^/        /'
    fi
}

permutations() {
    # permutations <prefix> <items...>: print every ordering, one per line.
    local prefix="$1" item x
    shift
    if [ $# -eq 0 ]; then
        echo "$prefix"
        return
    fi
    for item in "$@"; do
        local rest=()
        for x in "$@"; do [ "$x" = "$item" ] || rest+=("$x"); done
        permutations "$prefix $item" ${rest[@]+"${rest[@]}"}
    done
}

# --- checks --------------------------------------------------------------

echo "Build context hygiene"
leaks="$(find "$HERE/environment" \( -name '*.patch' -o -name 'tests_hidden' -o -name 'CLAUDE.md' \
    -o -name 'AGENTS.md' -o -name 'NOTES.md' -o -name 'ticket.md' \) -print)"
if [ -z "$leaks" ]; then ok "no hidden tests, patches or memory files"; else bad "found: $leaks"; fi

echo "Visible test-suite"
visible_suite "base"
visible_suite "base + all gold" "${TICKETS[@]}"

for t in "${TICKETS[@]}"; do
    echo "Ticket $t"
    others=()
    for x in "${TICKETS[@]}"; do [ "$x" = "$t" ] || others+=("$x"); done
    expect fail "base" "$tmp/$t-base" "$t"
    expect pass "base + own gold" "$tmp/$t-own" "$t" "$t"
    expect pass "base + all gold" "$tmp/$t-all" "$t" "${TICKETS[@]}"
    expect fail "base + all other gold" "$tmp/$t-others" "$t" "${others[@]}"
done

echo "Gold patches apply in every order"
perm_repo="$tmp/perm"
fresh_tree "$perm_repo"
git -C "$perm_repo" init -q
git -C "$perm_repo" add -A
git -C "$perm_repo" -c user.name=selfcheck -c user.email=selfcheck@localhost commit -qm base
orders=0
order_failures=0
trees=""
while IFS= read -r order; do
    orders=$((orders + 1))
    git -C "$perm_repo" reset -q --hard
    git -C "$perm_repo" clean -fdq
    for id in $order; do
        if ! git -C "$perm_repo" apply "$HERE/tickets/$id/gold.patch" 2>/dev/null; then
            order_failures=$((order_failures + 1))
            echo "        cannot apply $id in order:$order"
            break
        fi
    done
    git -C "$perm_repo" add -A
    trees="$trees $(git -C "$perm_repo" write-tree)"
done < <(permutations "" "${TICKETS[@]}")
distinct="$(printf '%s\n' $trees | sort -u | wc -l | tr -d ' ')"
if [ "$order_failures" -eq 0 ] && [ "$distinct" -eq 1 ]; then
    ok "$orders orders, all apply, one resulting tree"
else
    bad "$order_failures of $orders orders failed to apply; $distinct distinct resulting trees"
fi

echo
if [ "$failures" -eq 0 ]; then
    echo "selfcheck: all checks passed"
else
    echo "selfcheck: $failures check(s) FAILED"
    exit 1
fi
