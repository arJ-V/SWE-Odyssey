Reading an .ndx cache with full-length URL fields reads past the buffer
`binput()` returns `count + 1`, which assumes it consumed a separator. When it stops because it filled its `max` instead, that `+1` skips a byte that is not a separator and the caller's cursor drifts forward. In `PT_LoadCache__Old` the cursor ends up past the heap buffer holding the `.ndx`, and the next `binput` call reads out of bounds. The loop's `a < (use + ndxSize)` guard does not catch it, because the drift happens after the check.

Reproducer against master (5638910), with proxytrack built `-fsanitize=address`:

```sh
python3 - <<'PY'
with open("big.ndx", "w") as f:
    f.write("CACHE-1.4\n")
    f.write("Wed, 01 Jan 2025 00:00:00 GMT\n")
    f.write("h" * 1024 + "\n" + "f" * 1024 + "\n" + "0\n")
PY
: >big.dat
proxytrack --convert out.arc big.ndx
```

```
ERROR: AddressSanitizer: heap-buffer-overflow
READ of size 1
    #0 binput src/proxy/store.c:263
    #1 PT_LoadCache__Old src/proxy/store.c:1586
```

Only the legacy `.ndx` reader is affected, and fields that long take a hand-made or corrupt index, since anything HTTrack wrote is shorter. It is a read, not a write.

Found while fixing the neighbouring write overflow in #744, which bounded the second `binput` by the room the first left. That fix does not address this one: the first version of test 92 tripped it, so the committed test appends a trailing entry to isolate the field bound.

