# Requests to the atlas reset controller, included into setup.sh and test.sh.
#
# The controller has no network; it reads request files from the ctl volume (mounted here at
# $ATLAS_CTL_DIR) and answers in a response file. A request names an op and carries this step's
# token; the controller checks the token's hash and allows each op once per step.

ctl_request() {
    # ctl_request <op> <token> <timeout-sec> <data-json>: print the response; status 0 if "ok".
    local op="$1" token="$2" timeout="$3" data="${4:-}" id tmp resp i
    [ -n "$data" ] || data='{}'
    id="$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')"
    tmp="$ATLAS_CTL_DIR/.tmp-$id"
    resp="$ATLAS_CTL_DIR/resp-$id.json"
    [ -d "$ATLAS_CTL_DIR" ] || { echo "{\"ok\": false, \"error\": \"no $ATLAS_CTL_DIR\"}"; return 1; }
    printf '{"id": "%s", "op": "%s", "token": "%s", "data": %s}\n' "$id" "$op" "$token" "$data" > "$tmp"
    mv -f "$tmp" "$ATLAS_CTL_DIR/req-$id.json"
    for ((i = 0; i < timeout * 5; i++)); do
        [ -f "$resp" ] && break
        sleep 0.2
    done
    if [ ! -f "$resp" ]; then
        rm -f "$ATLAS_CTL_DIR/req-$id.json"
        echo "{\"ok\": false, \"error\": \"no answer from the controller within ${timeout}s\"}"
        return 1
    fi
    local body
    body="$(cat "$resp")"
    rm -f "$resp"
    printf '%s\n' "$body"
    case "$body" in *'"ok": true'*) return 0 ;; *) return 1 ;; esac
}
