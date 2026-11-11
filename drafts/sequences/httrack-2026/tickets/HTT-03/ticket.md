# A chunked response cut off before its last chunk is saved and cached as complete

httrack refuses to keep a response that is shorter than its `Content-Length`:
it logs `incomplete transfer (expected N bytes, got M bytes): file not cached,
will be retried on the next update`, retries, and on an `--update` keeps the
copy it already had. A response sent with `Transfer-Encoding: chunked` gets none
of this protection. If the connection closes after a complete data chunk but
before the terminating zero-length chunk, httrack treats the body as complete:
it writes the partial body to the mirror, caches it, and on `--update` the
partial body replaces the good copy from the previous crawl.

For chunked transfers this is detectable: the terminating `0` chunk is part of
the framing, so a stream that ends without it is truncated by definition. (A
plain HTTP/1.0 body that is only delimited by the connection closing has no
framing, so the same truncation cannot be detected there; that case is out of
scope.)

## How to reproduce

Use a local test server that answers `200` with `Transfer-Encoding: chunked`,
writes one data chunk and its CRLF, then closes the connection without sending
`0\r\n\r\n`.

1. Crawl a page that links to such an HTML page and to such a binary file:
   both are reported as fine, both land in the cache, the HTML page is saved.
2. Mirror a site normally, then change the server so that on the next fetch the
   page and the file are cut this way, and run `httrack --update`: the good
   copies are overwritten with the partial bodies.

## What should happen

A chunked response that ends without its terminating zero-length chunk must be
handled like a response shorter than its `Content-Length`, unless the user asked
for tolerant mode (`-%B`):

- It is never written to the cache, and on `--update` it never replaces the
  copy already in the mirror (the old content stays on disk, and the URL is
  retried on the next update, as the existing warning says).
- A warning is logged, worded like the existing incomplete-transfer warning,
  that starts with `truncated chunked transfer (terminating chunk missing, got N
  bytes)` and names the URL.
- The transfer is retried like a short `Content-Length` body. If it still fails,
  the final error for the link starts with `Truncated chunked transfer` (the
  usual error line then reads `... after N retries at link ...`).
- As for short `Content-Length` bodies on a first crawl: an HTML page held in
  memory must not reach the disk, while the bytes of a file that was being
  written straight to disk stay there, uncached (do not delete them).
- A connection reset in the middle of a chunked body keeps its current
  diagnostic (`Interrupted transfer`); it must not be relabelled as a
  truncated chunked transfer. Complete chunked responses must not produce the
  new warning at all.
- With `-%B` the truncated body is accepted and cached as today, without the new
  warning.

While you are in this code: a chunk-size line is untrusted input. A line such as
`80000000` (larger than a signed 32-bit int) currently goes through as a
negative size and ends in an allocation failure (`not enough memory (-...`).
Such a size must be rejected with the existing `Illegal chunk (...)` error for
that URL (`Illegal chunk (80000000) for <url>`), and no `not enough memory (-`
message may appear. A stream rejected this way never reached a valid
terminating chunk either, so it is a truncated chunked transfer as above: the
warning is logged, nothing is cached, and on `--update` the previous copy stays.
