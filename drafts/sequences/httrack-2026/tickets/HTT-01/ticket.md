# FTP downloads sometimes end up as empty files when several run in parallel

When I mirror a batch of FTP files with several connections, some files end up
**0 bytes on disk** although the crawl reports them as complete. The server
sent every byte and answered `226`, and `hts-cache/new.txt` lists the file as a
success with its full size (for example `4109/4109 ... 200 added ('OK')`). There
is no error in `hts-log.txt`.

It is intermittent and depends on load:

- default number of connections, idle machine: rarely or never;
- default number of connections while the machine is busy: roughly half of my
  runs lose at least one file;
- `-c1`: never.

When I turn on the debug log (`-Z`), the runs that lose files are the ones
where `hts-log.txt` shows `slots ready moved to background` lines, i.e. the
engine moved finished transfers out of memory while others were still running.
It happens on a first crawl too, so it has nothing to do with updates or
re-fetches.

## How to reproduce

1. Serve a dozen files over FTP from a local server (any small FTP server
   works). Give them different sizes and contents, a few KB each.
2. Mirror them all in one run with several connections and the debug log on:

   ```
   httrack ftp://127.0.0.1:2121/f1.bin ... ftp://127.0.0.1:2121/f12.bin \
       -O out -c8 -Z --robots=0 --timeout=20 --retries=1
   ```

3. Compare each mirrored file with the original (`cmp`). Repeat a few times;
   running something CPU-heavy at the same time makes it more likely.

## What happens

Some mirrored files are empty, although the log and the cache index say they
were transferred in full.

## What should happen

Every FTP file that was transferred completely must be on disk with exactly the
bytes the server sent, whatever the number of connections and whether or not
finished transfers were moved out of memory while the crawl was running. The
crawl must not report any error for these transfers. Files whose content was
still in memory when they were moved out must of course keep that content too.
