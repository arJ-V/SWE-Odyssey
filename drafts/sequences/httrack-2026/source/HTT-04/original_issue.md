A 304 revisit record names no peer IP
`warc_write_backtransaction()` reads the peer address from `back->r.address`, which the 304 path in `back_wait()` loses along with everything else the cache entry overwrites. `back_connxfr()` moves the socket and keep-alive members across the swap, not the address, so every revisit record comes out without the `WARC-IP-Address` a full response carries.

Visible on the mini304 fixture: crawl it with `--warc-file`, re-crawl with `--update --warc-file <other name>`, and the revisit records in the second archive have no `WARC-IP-Address` line while the first archive's response records do.

Found while fixing #826, which moves the stashed request and response headers across the same swap.
An engine-forced not-modified is archived as a server-not-modified revisit the server never sent
`back_wait()` forces `HTTP_NOT_MODIFIED` on its own in several places where the server said something else: the same-size hack, and the #176 path that ignores an error on update to keep the existing copy. The 304 branch then sets `notmodified`, and `warc_write_backtransaction()` turns that into a `revisit` record whose `WARC-Profile` is `.../revisit/server-not-modified`.

Nothing behind it is a 304. The block is a synthesized `HTTP/1.1 200 OK`, and the real exchange was a 200 with a body or a 403. A replay engine resolving that profile expects a server that answered 304, and `tests/warc-validate.py --revisit-exchange` (added in #837) rejects such a record.

Either the record should be an `identical-payload-digest` revisit against the previous capture, or the forced case should write no record at all and let the previous archive stand. Found while fixing #826; the fix there gates the header carry-over on `server_sent_304` so this case keeps its current output rather than gaining a contradictory one.
