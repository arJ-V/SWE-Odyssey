#!/usr/bin/env bash
# Network egress probe, run INSIDE the agent container (by probe_agent.py during
# the agent phase, or by hand with `docker exec`). Makes no authenticated call:
# provider endpoints are hit without credentials and must answer 401/403.
#
# One line per probe:  PROBE <name> <OPEN|BLOCKED|INFO> <detail>
#   OPEN     the request reached a server (an HTTP status, a DNS answer, a socket)
#   BLOCKED  it did not (timeout, refused, reset, resolve failure)
#   INFO     a fact, not a reachability result
# check_probe.py turns the lines into pass/fail against the expected policy.
set -u
T=12   # seconds per probe

GITHUB_IP=140.82.112.3      # a github.com front end (raw-IP probes skip DNS)
CF_IP=1.1.1.1
GOOGLE_DNS=8.8.8.8

say() { printf 'PROBE %s %s %s\n' "$1" "$2" "$(printf '%s' "${3:-}" | tr '\n\r' '  ' | cut -c1-300)"; }

# HTTP(S) probe: OPEN with the status code if any HTTP response came back.
http() {  # name, curl args...
    local name="$1"; shift
    local out code rc
    out="$(timeout $((T + 3)) curl -sS -o /dev/null -m "$T" -w '%{http_code} %{remote_ip}' "$@" 2>&1)"; rc=$?
    code="${out%% *}"
    if [ "$rc" -eq 0 ] && [ "$code" != "000" ]; then say "$name" OPEN "http=$out"
    else say "$name" BLOCKED "rc=$rc $out"; fi
}

# Raw TCP connect with bash /dev/tcp (no TLS, no proxy awareness).
tcp() {  # name, host, port
    if timeout "$T" bash -c "exec 3<>/dev/tcp/$2/$3 && printf 'HEAD / HTTP/1.0\r\n\r\n' >&3 && head -c 64 <&3" >/tmp/egress_tcp.$$ 2>&1; then
        if [ -s /tmp/egress_tcp.$$ ]; then say "$1" OPEN "bytes=$(head -c 40 /tmp/egress_tcp.$$)"
        else say "$1" BLOCKED "connected but no reply (proxy closed it)"; fi
    else say "$1" BLOCKED "$(head -c 120 /tmp/egress_tcp.$$)"; fi
    rm -f /tmp/egress_tcp.$$
}

pyprobe() {  # name, python code printing OPEN/BLOCKED <detail>
    local out
    out="$(timeout $((T + 3)) python3 -I -c "$2" 2>&1 | tail -1)"
    case "$out" in
        OPEN*) say "$1" OPEN "${out#OPEN }" ;;
        BLOCKED*) say "$1" BLOCKED "${out#BLOCKED }" ;;
        *) say "$1" BLOCKED "error: $out" ;;
    esac
}

say whoami INFO "$(id -u):$(id -g) $(hostname)"
say capeff INFO "$(grep CapEff /proc/self/status | awk '{print $2}')"
say resolv INFO "$(grep -v '^#' /etc/resolv.conf | tr '\n' ';')"
say proxy_env INFO "HTTPS_PROXY=${HTTPS_PROXY:-} HTTP_PROXY=${HTTP_PROXY:-} ALL_PROXY=${ALL_PROXY:-}"

# --- allowed: model providers, unauthenticated (expect 401/403/404) --------------
http allow_anthropic https://api.anthropic.com/v1/models
http allow_openai https://api.openai.com/v1/models

# --- system resolver ------------------------------------------------------------
for h in api.anthropic.com github.com; do
    r="$(timeout "$T" getent ahostsv4 "$h" | awk 'NR==1{print $1}')"
    if [ -n "$r" ]; then say "dns_$h" OPEN "$r"; else say "dns_$h" BLOCKED "no answer"; fi
done
rnd="odyssey-probe-$RANDOM$RANDOM.example.com"
r="$(timeout "$T" getent ahostsv4 "$rnd" 2>&1)"; rc=$?
say dns_random_name INFO "rc=$rc ${r:-no answer} (rc=2 is NXDOMAIN: the recursive resolver answered)"

# --- denied: code hosts, package indexes, search --------------------------------
http deny_github https://github.com/
http deny_github_repo https://github.com/xroche/httrack/commits/master
http deny_raw_githubusercontent https://raw.githubusercontent.com/xroche/httrack/master/README.md
http deny_codeload https://codeload.github.com/
http deny_api_github https://api.github.com/
http deny_pypi https://pypi.org/simple/
http deny_npm https://registry.npmjs.org/
http deny_google https://www.google.com/
http deny_github_http http://github.com/
out="$(timeout $((T + 5)) env GIT_TERMINAL_PROMPT=0 git -c http.lowSpeedTime="$T" ls-remote https://github.com/xroche/httrack.git HEAD 2>&1)"; rc=$?
if [ "$rc" -eq 0 ]; then say deny_git_https OPEN "$out"; else say deny_git_https BLOCKED "rc=$rc $out"; fi
out="$(timeout $((T + 5)) git ls-remote git://github.com/xroche/httrack.git HEAD 2>&1)"; rc=$?
if [ "$rc" -eq 0 ]; then say deny_git_proto OPEN "$out"; else say deny_git_proto BLOCKED "rc=$rc $out"; fi
tcp deny_tcp_github_ssh github.com 22

# --- denied: raw IPs (no DNS involved) ------------------------------------------
http deny_ip_https_github -k "https://$GITHUB_IP/"
http deny_ip_http_cloudflare "http://$CF_IP/"
tcp deny_tcp_ip_80 "$CF_IP" 80
tcp deny_tcp_ip_443 "$GITHUB_IP" 443

# --- denied: another DNS server, DoH -------------------------------------------
DNSQ='
import socket, struct, sys
def q(name):
    hdr = struct.pack(">HHHHHH", 0x1234, 0x0100, 1, 0, 0, 0)
    body = b"".join(bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\0" + struct.pack(">HH", 1, 1)
    return hdr + body
'
pyprobe deny_dns_udp_8888 "$DNSQ
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout($T)
try:
    s.sendto(q('github.com'), ('$GOOGLE_DNS', 53)); d, _ = s.recvfrom(512); print('OPEN', len(d), 'bytes')
except Exception as e: print('BLOCKED', type(e).__name__, e)"
pyprobe deny_dns_udp_1111 "$DNSQ
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout($T)
try:
    s.sendto(q('github.com'), ('$CF_IP', 53)); d, _ = s.recvfrom(512); print('OPEN', len(d), 'bytes')
except Exception as e: print('BLOCKED', type(e).__name__, e)"
pyprobe deny_dns_tcp_8888 "$DNSQ
s = socket.create_connection(('$GOOGLE_DNS', 53), timeout=$T)
try:
    m = q('github.com'); s.sendall(struct.pack('>H', len(m)) + m); d = s.recv(512)
    print('OPEN' if d else 'BLOCKED', len(d), 'bytes')
except Exception as e: print('BLOCKED', type(e).__name__, e)"
http deny_doh_cloudflare -H 'accept: application/dns-json' 'https://cloudflare-dns.com/dns-query?name=github.com&type=A'
http deny_doh_google 'https://dns.google/resolve?name=github.com&type=A'
http deny_doh_ip -k -H 'accept: application/dns-json' "https://$CF_IP/dns-query?name=github.com&type=A"
pyprobe deny_udp_ntp "
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout($T)
try:
    s.sendto(b'\x1b' + 47 * b'\0', ('$CF_IP', 123)); d, _ = s.recvfrom(512); print('OPEN', len(d), 'bytes')
except Exception as e: print('BLOCKED', type(e).__name__, e)"

# --- denied: allowed name used as a front (SNI / Host header spoofing) ----------
# TLS with SNI api.anthropic.com sent to a GitHub IP. If the proxy dialled the
# original IP, GitHub would answer the Host: github.com request.
out="$(timeout $((T + 3)) curl -sk -m "$T" -o /tmp/egress_body.$$ -w '%{http_code}' \
    --resolve "api.anthropic.com:443:$GITHUB_IP" -H 'Host: github.com' https://api.anthropic.com/ 2>&1)"; rc=$?
if [ "$rc" -eq 0 ] && grep -qi 'github' /tmp/egress_body.$$ 2>/dev/null; then say deny_sni_front OPEN "http=$out body mentions github"
else say deny_sni_front BLOCKED "rc=$rc http=$out (no GitHub content)"; fi
out="$(timeout $((T + 3)) curl -sS -m "$T" -o /tmp/egress_body.$$ -w '%{http_code}' \
    --resolve "api.anthropic.com:80:$GITHUB_IP" -H 'Host: github.com' http://api.anthropic.com/ 2>&1)"; rc=$?
if [ "$rc" -eq 0 ] && [ "$out" != "000" ] && grep -qi 'github' /tmp/egress_body.$$ 2>/dev/null; then say deny_host_front OPEN "http=$out body mentions github"
else say deny_host_front BLOCKED "rc=$rc http=$out (no GitHub content)"; fi
out="$(timeout $((T + 3)) curl -sk -m "$T" -o /tmp/egress_body.$$ -w '%{http_code}' \
    --resolve "github.com:443:$GITHUB_IP" --connect-to github.com:443:api.anthropic.com:443 https://github.com/ 2>&1)"; rc=$?
if [ "$rc" -eq 0 ] && grep -qi 'github' /tmp/egress_body.$$ 2>/dev/null; then say deny_connect_to OPEN "http=$out"
else say deny_connect_to BLOCKED "rc=$rc http=$out"; fi
rm -f /tmp/egress_body.$$

# --- denied: link-layer and ICMP side doors ------------------------------------
pyprobe deny_af_packet "
import socket
try:
    s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(3)); print('OPEN', 'AF_PACKET socket opened')
except Exception as e: print('BLOCKED', type(e).__name__, e)"
pyprobe deny_raw_ip "
import socket
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_UDP); print('OPEN', 'raw IP socket opened')
except Exception as e: print('BLOCKED', type(e).__name__, e)"
pyprobe deny_icmp_echo "
import socket, struct
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_ICMP); s.settimeout(5)
    s.sendto(struct.pack('>BBHHH', 8, 0, 0, 0, 1) + b'odyssey', ('$GOOGLE_DNS', 0)); d, _ = s.recvfrom(256); print('OPEN', 'echo reply', len(d), 'bytes')
except Exception as e: print('BLOCKED', type(e).__name__, e)"

# --- agent CLIs present and start without the network --------------------------
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
for cli in claude codex node; do
    p="$(command -v "$cli" 2>/dev/null)"
    if [ -z "$p" ]; then say "cli_$cli" BLOCKED "not on PATH"; continue; fi
    v="$(timeout 60 "$cli" --version 2>&1)"; rc=$?; v="$(printf '%s' "$v" | head -1)"
    if [ "$rc" -eq 0 ] && [ -n "$v" ]; then say "cli_$cli" OPEN "$p $v"; else say "cli_$cli" BLOCKED "rc=$rc $p $v"; fi
done
say done INFO "$(date -u +%FT%TZ)"
