A cache repair that recovers nothing still replaces the cache with an empty archive
`unzRepair()` (src/minizip/mztools.c:70) returns `Z_OK` with `entries == 0` when the source does not begin with a local file header: the scan loop breaks immediately, the end-of-central-directory record is written anyway, and `repair.zip` ends up a valid but empty 22-byte archive. Neither call site checks the entry count or opens `repair.zip` before committing it, so the rename replaces the users cache with that empty archive and prints a success message.

Repro:

```
mkdir -p p/hts-cache; printf "not a zip at all, some bytes" > p/hts-cache/new.zip
httrack -O p -#R http://127.0.0.1:1/
  Cache: 0 bytes successfully recovered in 0 entries
  exit=0 ; new.zip is now 22 bytes
```

The original bytes were still carvable by hand before the repair ran. `cache_init` (src/htscache.c:1065) has the same shape against `old.zip`, so a crawl then continues silently with an empty cache.

Two related gaps sit in the same block. `unzRepair` ignores `fclose(fpOut)`, so a full disk yields `Z_OK` over a truncated `repair.zip`. And at src/htscache.c:1072 the commit happens before the reopen, so "successfully recovered" is printed even when `hts_unzOpen_utf8()` then returns NULL.

Guarding on a nonzero entry count, and opening `repair.zip` before renaming it over the original, would close all three. Found while reviewing #817.
