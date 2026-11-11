# Cache repair replaces the user's cache with an empty archive, and reports success when the repaired cache cannot be moved into place

httrack repairs a damaged cache archive (`hts-cache/new.zip`) in two places: on
request with `httrack -O <project> -#R` (repair only), and automatically when a
crawl finds that the cache archive does not open (`Cache: damaged cache, trying
to repair` in `hts-log.txt`). Both paths have the same problems.

## 1. A repair that recovers nothing still replaces the cache

```
mkdir -p p/hts-cache
printf "not a zip at all, some bytes" > p/hts-cache/new.zip    # 28 bytes
httrack -O p -#R http://127.0.0.1:1/
Cache: 0 bytes successfully recovered in 0 entries
echo $?   # 0
```

`p/hts-cache/new.zip` is now a valid but **empty** 22-byte archive. The
original bytes, which could still have been carved by hand, are gone. The
automatic repair at crawl start does the same, and the crawl then silently
continues with an empty cache.

## 2. A repaired cache that cannot be moved into place is reported as a success

The repair writes `hts-cache/repair.zip` and then moves it over the damaged
archive. If that move fails (for example the rename is refused), httrack still
prints `... successfully recovered ...`, and the damaged cache may already have
been removed before the move was attempted, so the user ends up with no cache at
all. On systems where a rename cannot replace an existing file, the fallback
used to replace the file must not destroy the cache either.

## What should happen

For `-#R` and for the automatic repair alike:

- A successful repair (the damaged archive had at least one recoverable entry,
  the recovered archive opens, and it was moved into place) behaves as today:
  `Cache: N bytes successfully recovered in M entries`, the recovered archive is
  now `hts-cache/new.zip`, and no `hts-cache/repair.zip` is left behind. `-#R`
  exits 0.
- A recovery holding no entry, or a recovered archive that does not open, must
  never replace the cache: the damaged `new.zip` stays byte for byte as it was.
- If the recovered archive cannot be moved into place, the damaged `new.zip`
  stays as it was (it must not be deleted first), `hts-cache/repair.zip` is kept
  so the user can retry by hand, and no parked or temporary copy of the cache
  (`*.hts-old*`) is left in `hts-cache/`. This must also hold when the platform
  refuses to rename over an existing file and the replace has to go through its
  fallback.
- In every failure case, nothing may claim `successfully recovered`. `-#R`
  prints the reason on stderr (`Cache: <reason>`) and exits non-zero. The
  automatic repair writes `Cache: <reason>` to `hts-log.txt`; when the move
  into place failed, the reason reads `could not put the repaired cache in
  place`.
