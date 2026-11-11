A successful FTP transfer can be blanked when its backlog slot is swapped out
A completed FTP transfer can end as a 0-byte file while the engine records it as a success. `hts-cache/new.txt` says `4109/4109 ... 200 added ('OK')`, the server sent every byte and answered 226, and the file on disk is empty.

The FTP worker writes the body itself and leaves `r.is_write` at 0 with `r.adr == NULL` (`src/htsftp.c:630`). Once the slot turns `STATUS_READY`, `slot_can_be_cached_on_disk()` (`src/htsback.c:342`) accepts it, `back_cleanup_background()` serializes it into the on-disk ready table and calls `back_clear_entry()`. The slot that comes back has `size > 0`, no buffer and `is_write == 0`, so the later write of the link puts nothing over the bytes the worker had already produced.

It is a race against `back_clean()` landing between the slot going ready and the parser taking it, so it needs load. Three FTP URLs, same fixture, same machine (12 cores):

- default concurrency, idle: 0/25 runs lost a file
- default concurrency, 24 busy loops running: 7/15
- `-c1`, same load: 0/15

Reproducible with the FTP fixture added in #771 (`tests/ftp-server.py`): mirror three FTP files while the box is busy, then compare the mirrored sizes against the server root. `tests/97_local-ftp-refetch.test` pins `-c1` to stay clear of this.

Found while fixing #771 and independent of it: this hits a first crawl, where no re-fetch backup exists at all.
