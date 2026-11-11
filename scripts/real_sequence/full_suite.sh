#!/usr/bin/env bash
# Interaction experiment (drafts/docs/REAL_SEQUENCE_GUIDE.md, step 5): run the WHOLE test
# suite on the base plus some gold patches, in one container of the upstream
# image, and record one "<RESULT> <test>" line per test.
#
#   full_suite.sh <name> <gold-id>...
#
# Environment (all required unless noted):
#   IMAGE       upstream image to run in
#   BASE        host dir with the base tree (git archive of stack_reverts' work/ HEAD)
#   STESTS      host dir holding the snapshot's test dir (copied over BASE/$TESTDIR)
#   GOLD        host dir with <prefix><id>.patch files (stack_reverts' gold/)
#   OUT         host dir for <name>.txt results
#   PREFIX      gold file prefix (default xroche__httrack-)
#   TESTDIR     repo-relative test dir (default tests)
#   BUILD       build command run in the tree (default: autotools out of tree)
#   CHECK       suite command run in the tree (default: automake make check)
#   RESULTS     command printing "<RESULT> <test>" lines (default: read automake .trs)
#   SEQ         sequence id used to name the container (default exp)
set -u
name=$1
shift
: "${PREFIX:=xroche__httrack-}" "${TESTDIR:=tests}" "${SEQ:=exp}"
: "${BUILD:=autoreconf -fi >/dev/null 2>&1 && mkdir -p build && cd build && ../configure -q >/dev/null 2>&1 && make -j4 >/dev/null 2>&1}"
: "${CHECK:=cd build && HTTRACK_TEST_TIMEOUT=200 make -j8 -k check >/dev/null 2>&1}"
: "${RESULTS:=cd build/tests && for f in *.trs; do echo \"\$(grep :test-result: \$f | cut -d' ' -f2) \${f%.trs}\"; done | sort -k2}"
pats=""
for g in "$@"; do pats="$pats /gold/${PREFIX}$g.patch"; done
mkdir -p "$OUT"
docker run --rm --cpus 4 --network none --name "$SEQ-exp-$name" \
    -v "$BASE":/base:ro -v "$STESTS":/stests:ro -v "$GOLD":/gold:ro -v "$OUT":/runs \
    "$IMAGE" bash -c "
cp -a /base /w && cd /w || exit 2
for p in $pats; do git apply \$p || exit 3; done
rm -rf $TESTDIR && cp -a /stests $TESTDIR
( $BUILD ) || { echo BUILD-FAIL > /runs/$name.txt; exit 4; }
s=\$(date +%s); ( $CHECK ); e=\$(date +%s)
( $RESULTS ) > /runs/$name.txt
echo \"# secs \$((e-s))\" >> /runs/$name.txt
"
echo done >"$OUT/$name.done"
