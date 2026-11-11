# proxytrack: a bad position in a `.ndx` index serves the wrong record, and long cache paths overrun fixed buffers

Two problems in how `proxytrack` loads old-format caches (`.ndx` index + `.dat`
data file), seen with `proxytrack --convert out.arc <cache>`.

## 1. An unparseable position reuses the previous entry's offset

Each `.ndx` entry gives the offset of its record in the `.dat` file. If that
position field is not a number, the entry silently gets the offset of the
**previous** entry, so the URL is served (and converted) with another page's
headers and body. On the first entry there is no previous value and the result
is whatever happened to be in memory.

Reproduce: build a `.dat` with two records (say one with content type
`audio/midi` at offset 0 and one with `image/gif` right after it) and a `.ndx`
with two entries, `example.com/a.html` and `example.com/b.html`.

- Positions `0` and `<offset of the second record>`: `a.html` comes out as
  `audio/midi` and `b.html` as `image/gif`. Correct.
- Positions `<offset of the second record>` and `Z`: `b.html` also comes out as
  `image/gif`, copied from `a.html`'s entry.
- Positions `Z` and `<offset of the second record>`: `a.html` gets an arbitrary
  offset.

Expected: a position that does not parse reads as offset `0` (so in both cases
above the entry with the bad position resolves to the record at offset 0,
`audio/midi`).

## 2. Long cache paths are copied into 1024-byte buffers without a bound

proxytrack keeps the directory of a cache and the derived index/data filenames
in fixed 1024-byte fields. A cache stored under a deep enough directory (or an
`.ndx` name of a few thousand characters) is written past those fields. The
overrun stays inside one structure, so neither AddressSanitizer nor
`_FORTIFY_SOURCE` notices, but the memory behind it is corrupted.

Expected:

- `proxytrack --convert` on an `.ndx` whose name is 3000 characters long must
  not crash (the file does not even need to exist).
- A cache whose full path is just under 1024 bytes (for example three nested
  200-character directory names under `/tmp/...`) must still load normally:
  refusing it is as wrong as overflowing.
- A cache under a path longer than those fields (six such directory levels)
  must not crash the conversion. In these cases `proxytrack --convert` must
  finish normally (exit status 0), as it already does for a cache it cannot
  read.
- The `.zip` cache reader has the same base-path copy: a `.zip` cache at such a
  deep path must still be converted, its entries kept with an empty base path
  rather than dropped (for example an entry `example.com/page.html` with
  `Content-Type: text/html` must appear in the output `.arc` as `text/html`).
