# Process sweep, included into setup.sh and test.sh by `odyssey compile`.
#
# Anything an agent started (a timed-out harness, a background loop, a test
# server) must not keep running into the next segment or into grading, and
# matching process names is not enough. So every process is killed except
# PID 1, the calling script and its ancestors, and whatever was already
# running within 5 s of the container's boot (the keepalive and any services
# the image starts). Needs Linux /proc; elsewhere (host tests) it only acts
# with ODYSSEY_SWEEP_MATCH set and then kills only matching command lines.

proc_stat() {
    # proc_stat <pid>: set STAT_PPID and STAT_START from /proc/<pid>/stat.
    local line rest
    read -r line < "/proc/$1/stat" 2>/dev/null || return 1
    rest="${line##*) }"          # the fields after "(comm) "
    set -f
    # shellcheck disable=SC2086
    set -- $rest
    set +f
    STAT_PPID="${2:-0}"
    STAT_START="${20:-0}"
}

sweep_targets() {
    # Print the pids to kill, one per line. Runs in a $(...) subshell, so its
    # own pid ($BASHPID; bash >= 4) is kept as well as the script's ($$) and ancestors.
    local keep=" 1 ${BASHPID:-} " pid="$$" boot_ticks hz window d cmd
    if [ ! -d /proc/1 ]; then
        # Not Linux (a host test): only with an explicit match, via ps.
        [ -n "$ODYSSEY_SWEEP_MATCH" ] || return 0
        ps -A -o pid= -o args= | while read -r pid cmd; do
            case "$cmd" in *"$ODYSSEY_SWEEP_MATCH"*) [ "$pid" != "$$" ] && echo "$pid";; esac
        done
        return 0
    fi
    while [ -n "$pid" ] && [ "$pid" != 0 ] && [ "$pid" != 1 ]; do
        keep="$keep$pid "
        proc_stat "$pid" || break
        pid="$STAT_PPID"
    done
    proc_stat 1 || return 0
    hz="$(getconf CLK_TCK 2>/dev/null || echo 100)"
    window=$((5 * hz))
    boot_ticks="$STAT_START"
    for d in /proc/[0-9]*; do
        pid="${d#/proc/}"
        case "$keep" in *" $pid "*) continue ;; esac
        proc_stat "$pid" || continue
        [ "$STAT_START" -gt $((boot_ticks + window)) ] || continue
        if [ -n "$ODYSSEY_SWEEP_MATCH" ]; then
            cmd="$(tr '\0' ' ' < "$d/cmdline" 2>/dev/null || true)"
            case "$cmd" in *"$ODYSSEY_SWEEP_MATCH"*) ;; *) continue ;; esac
        fi
        echo "$pid"
    done
}

sweep_processes() {
    [ "$ODYSSEY_STOP_AGENTS" = 1 ] || return 0
    local pass pid
    # Two passes: a parent killed in the first may have forked in between.
    for pass in 1 2; do
        for pid in $(sweep_targets); do
            kill -9 "$pid" 2>/dev/null || true
        done
    done
}
