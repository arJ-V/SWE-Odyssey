An FTP resume can splice a changed remote when there is no cache entry
When httrack holds a local FTP file but no cache entry for it (`--cache=0`, or an `hts-cache` that was deleted), `back_add()` takes `range_req_size` from the file size at src/htsback.c:2074 and the transfer resumes with `REST`. If the remote changed since that local copy was written, the server appends the new tail onto the old head, and the mirrored file ends up as neither version.

PR #810 fixes the neighbouring case, a complete mirror resumed under `--update`, but leaves this one alone on purpose. The cache entry is what distinguishes a stale local copy from a genuine partial, so with no cache there is nothing to check against. HTTP does not have the problem because the server can reject a conditional request with 412 or 416. FTP has no conditional retrieval, so the client has to make the call itself.

This is not a regression. Before #810 the same splice was reachable through the `fexist()` test. Found while reviewing #810.
