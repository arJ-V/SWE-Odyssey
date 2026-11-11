A query-string character reference the page charset cannot represent is left unescaped, changing the query's parse
On a document with no declared charset (fallback iso-8859-1), HTTrack leaves an HTML character reference that iso-8859-1 cannot represent as literal source text in the query string, instead of percent-encoding it. The bare `&` and `#` then act as query delimiters, so the URL HTTrack records and replays parses differently from the one the document expressed.

Reproducer, page served as `text/html` with no charset:

    <a href="q.php?a=&euro;&c=&eacute;">q</a>

Request sent:

    GET /q.php?a=&euro;&c=%e9 HTTP/1.1

Control, same markup on a page declaring `charset=utf-8`:

    GET /q.php?a=%e2%82%ac&c=%c3%a9 HTTP/1.1

The origin sees three parameters in the first case (`a=` empty, a bare `euro;` flag, `c=%e9`) where the document expressed two, `a=€` and `c=é`. `&eacute;` comes through because iso-8859-1 covers it; `&euro;` is the one that falls through.

The URL Standard's percent-encode after encoding algorithm requires a code point the encoding cannot represent to be written as `%26%23`, the code point in decimal, then `%3B`. Under iso-8859-1 that makes `€` into `%26%238364%3B`, never a bare `&#8364;` and never an undecoded `&euro;`.

`hts_unescapeEntitiesWithCharset` decodes the entity, fails to convert it through `hts_convertStringFromUTF8`, leaves `len` at 0 and falls through to copying the raw source bytes: src/htsencoding.c:135-140, reached from src/htsparse.c:2157-2164.

Direction: emit `%26%23<decimal>%3B` when the conversion fails. Nothing in the tree does this today; `hts_convertStringFromUTF8` has one engine caller.

