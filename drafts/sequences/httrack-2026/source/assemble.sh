#!/usr/bin/env bash
# Rebuild tickets/<id>/{gold.patch,tests/files,tests/run.sh} from a stack_reverts.py
# output directory (drafts/docs/REAL_SEQUENCE_GUIDE.md, step 6). ticket.md is hand-written
# and left alone.
#
#   bash drafts/sequences/httrack-2026/source/assemble.sh <stack-out-dir>
set -euo pipefail
stack="${1:?usage: assemble.sh <stack_reverts.py --out dir>}"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
seq="$(cd "$here/.." && pwd)"
tools="$(cd "$seq/../../../scripts/real_sequence" && pwd)"

# P2P tests every ticket runs: quick, broad, and independent of every ticket.
CORE="00_runnable.test,01_engine-mime.test,01_engine-parse.test,01_engine-savename.test,15_local-types.test,18_local-update.test"
PROXYTRACK="92_local-proxytrack-ndx-fields.test,86_local-proxytrack-cache-longfields.test,87_local-proxytrack-nodate.test,53_local-proxytrack-arc-reason.test,53_local-proxytrack-cache-corrupt.test,118_local-proxytrack-arcwrite.test,79_local-proxytrack-webdav-mime.test,84_webhttrack-mirror-verbatim.test"

assemble() {
    # assemble <ticket> <instance number> <F2P,...> <area P2P,...>
    local t="$1" i="$2" f2p="$3" p2p="$4" d="$seq/tickets/$1"
    rm -rf "$d/tests/files" "$d/gold.patch"
    mkdir -p "$d/tests/files"
    cp "$stack/gold/xroche__httrack-$i.patch" "$d/gold.patch"
    cp -a "$stack/hidden/xroche__httrack-$i/." "$d/tests/files/"
    python3 -I "$tools/render_run_sh.py" --template "$tools/templates/run_automake.sh" \
        --sequence httrack-2026 --ticket "$t" --f2p "$f2p" --p2p "$CORE,$p2p" --out "$d/tests/run.sh"
}

assemble HTT-01 809 110_local-ftp-parallel.test \
    01_engine-backswap.test,01_engine-ftp-line.test,01_engine-ftp-userpass.test,102_local-ftp-refetch.test,111_local-ftp-update-rest.test,36_local-bigcrawl.test,33_local-delayed.test,55_local-chunked.test
assemble HTT-02 830 121_local-ftp-nocache-splice.test \
    01_engine-ftp-line.test,01_engine-ftp-userpass.test,01_engine-gmtime.test,102_local-ftp-refetch.test,111_local-ftp-update-rest.test,20_local-resume-loop.test,24_local-resume-overlap.test
assemble HTT-03 851 130_local-chunked-truncated.test \
    55_local-chunked.test,22_local-broken-size.test,43_local-update-truncate.test,54_local-update-truncate-purge.test,51_local-update-codec.test,50_local-contentcodings.test,47_local-crange-overflow.test,48_local-crange-memresume.test,44_local-update-errormask.test
assemble HTT-04 849 134_local-warc-revisit-ip.test,135_local-warc-forced-notmodified.test,136_local-warc-cache-priority.test \
    73_local-warc.test,74_local-warc-verbatim.test,74_local-warc-wacz.test,97_local-warc-update-keep.test,98_local-warc-segments.test,122_local-warc-revisit-headers.test,01_zlib-warc.test,01_zlib-warc-cdx.test,01_engine-warc-surt.test,104_engine-warc-longurl.test,38_local-update-304.test,44_local-update-errormask.test
assemble HTT-05 811 117_local-proxytrack-ndx-cursor.test "$PROXYTRACK"
assemble HTT-06 835 119_local-proxytrack-ndx-fields2.test "$PROXYTRACK"
assemble HTT-07 829 120_local-proxytrack-webdav-default.test "$PROXYTRACK"
assemble HTT-08 876 142_webhttrack-content-type.test \
    61_webhttrack-locale.test,63_webhttrack-home.test,68_webhttrack-outdir-charset.test,77_webhttrack-redirect.test,78_webhttrack-sid.test,81_webhttrack-maxsize.test,82_webhttrack-browse-links.test,83_webhttrack-argescape.test,84_webhttrack-mirror-verbatim.test,85_webhttrack-projpath.test,89_webhttrack-error-overflow.test,90_webhttrack-checkbox-clear.test,91_webhttrack-directory.test,141_webhttrack-warc-options.test
assemble HTT-09 856 139_local-query-charref.test \
    01_engine-charset.test,01_engine-entities.test,01_engine-idna.test,01_engine-unescape-bounds.test,41_local-utf8-link.test,21_local-intl-update.test,137_local-charsetless-encoding.test,26_local-strip-query.test,01_engine-stripquery.test,30_local-fragment-link.test,01_engine-urlhack.test,64_local-intl-outdir.test
assemble HTT-10 817 106_engine-repair-rename.test \
    01_zlib-cache.test,01_zlib-cache-corrupt.test,01_zlib-cache-golden.test,01_zlib-cache-legacy.test,01_zlib-cache-writefail.test,01_zlib-repair-shift.test,71_local-crange-repaircache.test,01_engine-renameover.test,02_update-cache.test,37_local-cache-outage.test,01_engine-cacheindex.test,01_engine-reconcile.test
