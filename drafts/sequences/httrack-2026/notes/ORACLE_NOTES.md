# HTTrack: orientation for a new maintainer

Read `AGENTS.md` at the repo root too; it is the project's own checklist. This
is the short version plus what usually costs newcomers an afternoon.

## Layout
- `src/`: the engine (`libhttrack`) and the programs. `httrack` is a thin
  `main` over the library; `htsserver` (WebHTTrack's small web server, GUI pages
  under `html/server/`) and `proxytrack` (`src/proxy/`, serves and converts
  caches and ARC/WARC files) are separate programs built from the same tree.
- Engine map, roughly: `htscoremain.c` parses the command line, `htscore.c` runs
  the crawl loop and saves files, `htsback.c` is the transfer backlog (parallel
  connections, HTTP response reading, finalising a download), `htsparse.c` is
  the HTML/link parser, `htsname.c` decides local file names, `htscache.c` reads
  and writes `hts-cache/` (zip archives via `minizip/`), `htsftp.c` is the FTP
  client, `htswarc.c` writes WARC/CDX/WACZ, `htscharset.c`/`htsencoding.c` do
  charsets and entities, `htslib.c` is the grab bag (HTTP, dates, files, logs).
- `src/coucal/` is the hash table library (vendored submodule; leave it alone).
- `tests/`: the automake test suite. `lang/`, `html/`, `man/`: translations,
  docs and GUI assets (some are byte-sensitive ISO-8859-1, see `AGENTS.md`).

## Build
- `./bootstrap` once (autoreconf), then build out of tree:
  `mkdir -p build && cd build && ../configure && make -j8`. A full build takes
  well under a minute; there is no need to install anything.
- Generated files (`configure`, `Makefile.in`, objects) are git-ignored; never
  commit them. After editing a `Makefile.am` or `configure.ac`, re-run
  `./bootstrap`.
- Binaries end up in `build/src/` (`httrack`, `htsserver`, `proxytrack`), the
  library in `build/src/.libs/`. Use `build/libtool --mode=execute gdb ...` to
  debug the uninstalled binaries.

## Tests
- `make -C build check -j8` runs everything (about two minutes). One or a few:
  `make -C build/tests check TESTS="00_runnable.test 15_local-types.test"`.
  Results: `build/tests/<name>.log` (output) and `.trs` (PASS/FAIL/SKIP).
- A hand-run `.test` does not get `build/src` on `PATH`; go through `make check`
  or set `PATH=$PWD/build/src:$PATH` yourself.
- `01_engine-*` tests call hidden engine self-tests (`httrack -#test=NAME`, list
  them with `httrack -#test`); they are fast and need no network.
- `NN_local-*` tests crawl a local fixture server (`tests/local-server.py`, one
  route per scenario; `tests/ftp-server.py` for FTP) through
  `tests/local-crawl.sh`, whose options (`--found`, `--log-found`,
  `--cache-found`, `--rerun-args '--update'`, ...) are documented at its top.
  They need python3 and only loopback networking.
- `HTTRACK_TEST_TIMEOUT` (seconds) bounds each test; a hung test fails with a
  dump instead of hanging the run.
- Network tests (`1x_crawl-*`) skip themselves offline; that is expected.

## Conventions and gotchas
- Use the `*t` allocator wrappers (`malloct`, `freet`, ...) and the bounded
  string helpers from `htssafe.h`; know which ones abort on overflow and which
  clip (see `AGENTS.md`), and clip for anything read from the wire or a cache.
- Log through `hts_log_print(opt, LOG_..., ...)`; the crawl log is
  `<project>/hts-log.txt`, debug lines need `-Z`. Tests grep these messages, so
  keep the wording of existing ones stable.
- A crawl's state lives in `<project>/hts-cache/` (`new.zip`, `new.txt`,
  `new.lst`/`old.lst`); `--update` re-crawls against it.
- Many comments are in French; translate the ones you touch.
- Format only the lines you change (`git clang-format`), never whole files.
