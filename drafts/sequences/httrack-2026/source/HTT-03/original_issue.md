A chunked response cut at a chunk boundary is stored and cached as complete
`back_finalize()`'s incomplete-transfer guard (`src/htsback.c`) is gated on `r.totalsize >= 0`, so it only fires for a response that declared a Content-Length. A chunked response has `totalsize == -1` and never reaches it. That is correct for the general case, but it means the engine cannot tell a complete chunked body from a truncated one, and it assumes complete.

Send `200` with `Transfer-Encoding: chunked`, write one data chunk and its trailing CRLF, then close the connection without the terminating zero-length chunk. `back_wait` sets `r.size == r.totalsize` at the chunk boundary, `READ_EOF` then sets `STATUS_READY`, and `statuscode` stays 200 because the error path only rewrites it when `statuscode <= 0`. `back_finalize` takes the success branch: the partial body is written to the mirror, cached, and on an `--update` it replaces whatever was there before.

For chunked this is detectable. The terminating chunk is part of the framing, so a stream that ended without it is truncated by definition, and the engine could refuse to store it the same way it refuses a short Content-Length body today.

An HTTP/1.0 identity body delimited only by connection close has no framing to check, so the same truncation is not knowable at the protocol level. A fix should say so rather than imply it covers that case too.

Found while fixing #775. That issue's guard makes the commit conditional on the new copy existing, which catches a `filecreate()` that failed outright. Existence is not completeness, so it does not catch this.

