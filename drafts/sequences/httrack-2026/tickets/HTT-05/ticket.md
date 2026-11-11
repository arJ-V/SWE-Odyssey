# proxytrack mis-reads legacy `.ndx` cache indexes whose fields are exactly at their size limit

`proxytrack --convert out.arc cache.ndx` (which needs the sibling `cache.dat`
next to the index) reads the old `.ndx`/`.dat` cache format. When a field in the
index is exactly as long as the reader's limit for it, or when the index ends in
the middle of a field without a final newline, the reader loses its place: it
silently skips one byte that is not a line separator, so the next field starts
one byte late. At the end of the file it steps past the end of the buffer
holding the index; a build with `-fsanitize=address` reports a
heap-buffer-overflow (a 1-byte read) in the index reader.

Reproducer (ASan build):

```sh
python3 - <<'PY'
with open("big.ndx", "w") as f:
    f.write("CACHE-1.4\n")
    f.write("Wed, 01 Jan 2025 00:00:00 GMT\n")
    f.write("h" * 1024 + "\n" + "f" * 1024 + "\n" + "0\n")
PY
: > big.dat
proxytrack --convert out.arc big.ndx
```

Indexes written by httrack never have fields this long, so this takes a
hand-made or corrupt index, but the reader must not misbehave on it.

The limits involved, as far as I can tell: the header line is read into 256
bytes; an entry's URL is read in two parts into one 2048-byte buffer, the first
part up to 1024 bytes and the second part up to whatever room the first part
left (2048 - first - 1); the position field is read into 200 bytes.

## What should happen

A field that is cut at its limit is truncated, and the reader carries on at the
very next byte: it only steps over a real line separator, never over a byte it
did not consume, and never past the end of the index. With the example cases
below, `proxytrack --convert` must finish normally and report the number of
items it built (`(N items added)`, or `(no items added)`):

| index content | expected |
|---|---|
| valid header, then two entries whose URLs are 1023 `h` followed by `A` and `B` respectively (empty second line, positions `10` and `20`) | 2 items |
| same with 1024 `h` before the `A`/`B` | 2 items (the two URLs must stay distinct) |
| a first line of 257 or 258 `C` characters instead of the header, followed by three entries `a/1`,`x`,`1` / `b/2`,`y`,`2` / `c/3`,`z`,`3` | 3 items |
| valid header, a 10-byte first URL line, then 2037 `f` characters ending the file (no newline) | 1 item |
| valid header, `h/1`, `x`, then 200 `9` characters ending the file | 1 item |
| `0`, `SKIP`, then 1023 `h` characters ending the file | 1 item |
| the reproducer above (two 1024-character lines, then `0`) | 1 item |
| a file holding a single NUL byte | no items |

(Each `.ndx` needs an empty `.dat` with the same base name next to it.)
