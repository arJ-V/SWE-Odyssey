# WARC output on `--update`: revisit records lose the peer IP, and "not modified" is claimed for 304s the server never sent

I archive my mirrors with `--warc-file` and re-run them with `--update`. The
update archives have two problems.

## 1. Revisit records for real 304s have no `WARC-IP-Address`

Crawl a site with `--warc-file first`, then re-crawl it with
`--update --warc-file second` against a server that answers the conditional
requests with `304 Not Modified`. The `revisit` records in `second.warc.gz` have
no `WARC-IP-Address` header, while the `response` records of the first archive
do (`127.0.0.1` for a local server). A revisit for a real exchange with a server
should name the peer exactly like a response record does.

## 2. "server-not-modified" revisits for exchanges that were not 304s

On `--update`, httrack sometimes decides by itself that a page did not change
and keeps the existing copy, although the server never said `304`. Examples:

- the server answers the conditional request with an error (for example `403`
  with an error page) and httrack keeps the good copy it already had;
- cache-priority mode (`-C1`), where the copy is taken from the cache and no
  request is sent at all.

In the WARC these URLs come out as `revisit` records with
`WARC-Profile: http://netpreserve.org/warc/1.1/revisit/server-not-modified` and
a synthesized `HTTP/1.1 200 OK` block. That is wrong: nothing behind them is a
304, and a replay tool resolving that profile expects a server that answered
304. A strict validator rejects such records ("revisit ... does not carry the
304 it stands for").

## What should happen

On `--update` with `--warc-file`:

- A URL the server really answered with `304` is still written as a
  `server-not-modified` revisit (carrying the 304), and it now has the
  `WARC-IP-Address` of the server, like a response record. No `response` record
  is written for it.
- A URL that httrack itself treated as unchanged (error masked on update,
  `-C1` cache priority, or any other case where the server did not send a 304)
  is written as a revisit with
  `WARC-Profile: http://netpreserve.org/warc/1.1/revisit/identical-payload-digest`,
  pointing at the same target URI and carrying the payload digest of the copy
  that was kept, and no `response` record. This includes a file whose body is
  genuinely empty: an empty payload still has a well-defined digest.
- If the build cannot compute payload digests (built without OpenSSL), there is
  nothing such a revisit could point at: write no record at all for those URLs
  (real 304s keep their `server-not-modified` revisit).
- These engine-decided URLs were not captured in this pass either, so they
  count like the 304 revisits when httrack decides whether this pass's archive
  may replace an earlier one of the same name. Re-running a crawl where every
  URL is kept this way, with the same `--warc-file` name as the first pass, must
  leave the first pass's `.warc.gz` and `.cdx` untouched (byte-identical, no
  temporary files left), instead of replacing them with an almost empty archive.
- The kept copy in the mirror is unchanged in all of these cases (for example
  the good body stays on disk, never the 403 error page), and a fresh first
  crawl still archives full `response` records with their bodies.
