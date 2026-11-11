#!/usr/bin/env python3
"""PID 1 of atlas-commerce's main container: reaps orphans and stops agent leftovers before grading.

Harbor runs each step's verifier in this container, after the agent's turn: it empties
/logs/verifier and runs `chmod 777 /logs/verifier`, empties /tests, uploads the step's tests and
then runs /tests/test.sh. A process the agent left running could rewrite the uploaded test.sh in
that window, or the reward files after it, so test.sh's own process sweep comes too late.

This guard watches /logs/verifier with inotify. The `chmod` (IN_ATTRIB on the directory
itself) marks the start of a verifier phase; at that moment it kills every process except
PID 1, Harbor's verifier-reset command, and docker-exec sessions started after that moment (they
are Harbor's: the agent cannot start one), and keeps doing so for a few seconds so a forking
leftover cannot outrun it. Processes are classified by their docker-exec root: walking the
parent chain inside the container ends at PPID 0 for anything Harbor started with
`docker exec` and at PID 1 for orphans (anything that outlived the session that started it).

As PID 1 of its PID namespace it cannot be killed from inside the container: the kernel drops
SIGKILL and SIGSTOP sent to a namespace's init from within, and every catchable signal is
ignored here. `docker stop` still works (compose sets a short stop_grace_period).

Records go to /run/atlas-guard/sweeps.jsonl, which test.sh copies into the verifier output.
"""

import ctypes
import json
import os
import select
import signal
import struct
import time

WATCH_DIR = os.environ.get("ATLAS_GUARD_WATCH", "/logs/verifier")
STATE_DIR = "/run/atlas-guard"
LOCKDOWN_SEC = 5.0
IN_ATTRIB = 0x00000004
IN_IGNORED = 0x00008000
HARBOR_RESET_MARK = b"chmod 777 " + WATCH_DIR.encode()

HZ = os.sysconf("SC_CLK_TCK")


def _ignore_signals() -> None:
    for name in ("SIGTERM", "SIGINT", "SIGHUP", "SIGQUIT", "SIGUSR1", "SIGUSR2", "SIGTSTP", "SIGTTIN",
                 "SIGTTOU", "SIGPIPE", "SIGALRM", "SIGXCPU", "SIGXFSZ", "SIGVTALRM", "SIGPROF"):
        try:
            signal.signal(getattr(signal, name), signal.SIG_IGN)
        except (AttributeError, OSError, ValueError):
            pass


def _uptime_ticks() -> int:
    with open("/proc/uptime", encoding="ascii") as handle:
        return int(float(handle.read().split()[0]) * HZ)


def _stat(pid: int):
    """(ppid, start_ticks) of a live process; None for a zombie or a vanished pid."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as handle:
            data = handle.read()
    except OSError:
        return None
    rest = data[data.rfind(b")") + 2:].split()
    if rest[0] in (b"Z", b"X"):
        return None
    return int(rest[1]), int(rest[19])


def _cmdline(pid: int) -> bytes:
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as handle:
            return handle.read().replace(b"\0", b" ")
    except OSError:
        return b""


def _processes() -> dict:
    procs = {}
    for entry in os.listdir("/proc"):
        if entry.isdigit():
            info = _stat(int(entry))
            if info:
                procs[int(entry)] = info
    return procs


def _exec_root(pid: int, procs: dict):
    """The topmost in-container ancestor: (root_pid, root_ppid). root_ppid 0 = a docker exec, 1 = orphaned."""
    seen = set()
    while pid in procs and pid not in seen:
        seen.add(pid)
        ppid = procs[pid][0]
        if ppid in (0, 1):
            return pid, ppid
        pid = ppid
    return pid, 1


def sweep(trigger_ticks: int) -> list:
    me = os.getpid()
    killed = []
    procs = _processes()
    for pid, (ppid, start) in procs.items():
        if pid in (1, me):
            continue
        root, root_ppid = _exec_root(pid, procs)
        if root_ppid == 0:
            root_start = procs.get(root, (0, 0))[1]
            if root_start >= trigger_ticks or HARBOR_RESET_MARK in _cmdline(root):
                continue  # Harbor: the verifier reset itself, or a session started after it
        cmd = _cmdline(pid)[:200].decode("utf-8", "replace")
        try:
            os.kill(pid, signal.SIGKILL)
            killed.append({"pid": pid, "cmd": cmd, "exec_root": root, "orphan": root_ppid != 0})
        except OSError:
            pass
    return killed


def _reap() -> None:
    while True:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if pid == 0:
            return


def _record(entry: dict) -> None:
    try:
        os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)
        with open(os.path.join(STATE_DIR, "sweeps.jsonl"), "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
    except OSError:
        pass


def _inotify():
    libc = ctypes.CDLL(None, use_errno=True)
    fd = libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
    if fd < 0:
        return None
    if libc.inotify_add_watch(fd, WATCH_DIR.encode(), IN_ATTRIB) < 0:
        os.close(fd)
        return None
    return fd


def _attrib_on_dir(fd: int) -> bool:
    """Drain the inotify fd; True if the watched directory itself changed attributes."""
    hit = False
    try:
        data = os.read(fd, 65536)
    except BlockingIOError:
        return False
    offset = 0
    while offset + 16 <= len(data):
        _wd, mask, _cookie, length = struct.unpack_from("iIII", data, offset)
        if mask & IN_ATTRIB and length == 0:
            hit = True
        offset += 16 + length
    return hit


def main() -> None:
    _ignore_signals()
    os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)
    fd = None
    last_mode = None
    lockdown_until = 0.0
    trigger_ticks = 0
    started = time.time()
    _record({"event": "start", "time": started, "watch": WATCH_DIR})
    while True:
        try:
            if fd is None and os.path.isdir(WATCH_DIR):
                fd = _inotify()
                if fd is not None:
                    _record({"event": "watching", "time": time.time()})
            triggered = False
            if fd is not None:
                ready, _, _ = select.select([fd], [], [], 0.05 if lockdown_until else 0.5)
                if ready and _attrib_on_dir(fd):
                    triggered = True
            else:
                # Fallback without inotify: poll the directory's ctime.
                time.sleep(0.05)
                try:
                    mode = os.stat(WATCH_DIR).st_ctime_ns
                except OSError:
                    mode = None
                if last_mode is not None and mode != last_mode:
                    triggered = True
                last_mode = mode
            now = time.time()
            if triggered:
                trigger_ticks = _uptime_ticks()
                lockdown_until = now + LOCKDOWN_SEC
                killed = sweep(trigger_ticks)
                for _ in range(20):
                    if not killed:
                        break
                    _reap()
                    more = sweep(trigger_ticks)
                    killed += more
                    if not more:
                        break
                _record({"event": "verifier-start", "time": now, "killed": killed})
            elif lockdown_until and now < lockdown_until:
                killed = sweep(trigger_ticks)
                if killed:
                    _record({"event": "lockdown", "time": now, "killed": killed})
            elif lockdown_until:
                lockdown_until = 0.0
            _reap()
        except Exception as exc:  # PID 1 must never exit
            _record({"event": "error", "time": time.time(), "error": repr(exc)})
            time.sleep(0.5)


if __name__ == "__main__":
    main()
