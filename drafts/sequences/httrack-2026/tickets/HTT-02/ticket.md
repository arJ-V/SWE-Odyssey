# FTP resume without a cache splices a changed remote file onto the old local copy

I mirror an FTP file with the cache turned off (`--cache=0`; the same happens if
`hts-cache` was deleted). If a local copy of the file already exists, httrack
resumes the download from the size of that local copy (it sends `REST <size>`).
That is right when the local file is a genuine partial download of the same
remote file, but httrack does it whatever the local file is. If the remote file
changed since the local copy was written, the server sends the tail of the
**new** file and httrack appends it to the head of the **old** one: the mirrored
file is then neither version.

FTP has no conditional download, so the client has to decide on its own whether
a resume is safe. Today it does not check anything.

## How to reproduce

All runs use `--cache=0`, against a local FTP server that supports `SIZE` and
`MDTM`.

1. Serve `a.bin` (8 KB) and let the first transfer die half way, so a partial
   file is left in the mirror.
2. Run again with the server healthy: the partial copy is resumed (`REST` with
   the partial size) and the result equals the served file. This must keep
   working.
3. Replace `a.bin` on the server with a longer file with different content and
   a later modification time. Run again: httrack sends `REST` and the mirror
   ends up with the old bytes followed by the tail of the new file.

Other cases that end the same way:

- the remote changed but kept the same length and modification time as the
  local copy;
- the server does not answer `MDTM` at all;
- the local copy was not written by httrack (restored from an archive, copied
  without preserving times) and so carries a local-clock date, typically newer
  than the remote.

## What should happen

Without a cache entry, httrack must only resume an FTP download when the server
proves the local copy can still be the beginning of the remote file:

- the remote size (`SIZE`) is larger than the local copy, **and**
- the remote modification time (`MDTM`, UTC) is exactly the modification time
  of the local copy. "Local copy not newer than the remote" is not enough.

If either check fails, or the server gives no usable `MDTM` reply, httrack must
download the whole file (`RETR` without `REST`) and replace the local copy, with
no error reported in `hts-log.txt`.

For the date comparison to mean anything, httrack must stamp the files it
downloads over FTP with the server's `MDTM` time, as it already does with
`Last-Modified` for HTTP. That includes a partial file left by an interrupted
transfer, so that the next run can resume it. A retried transfer must not reuse
the date of a previous attempt.
