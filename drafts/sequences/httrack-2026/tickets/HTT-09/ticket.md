# A character reference in a link's query string that the page charset cannot represent breaks the query

When a link's query string contains an HTML character reference (named like
`&euro;` or numeric like `&#8364;`) that the page's charset cannot represent,
httrack copies the reference into the request as literal text. Its `&` and `#`
then act as query delimiters, so the server receives different parameters from
the ones the page expressed.

## How to reproduce

Serve this page as `text/html` with **no** charset declared (httrack then falls
back to iso-8859-1):

```html
<a href="qnone.html?a=&euro;&b=&#8364;&c=&eacute;&d=x">q</a>
```

and crawl it. httrack requests:

```
GET /qnone.html?a=&euro;&b=&#8364;&c=%e9&d=x
```

(`&#8364;` may also come out as a bare `&#8364;` or similar source text.) The
server parses more parameters than the page wrote: an empty `a=`, a bare
`euro;` flag, and so on. `&eacute;` is fine because iso-8859-1 has `é`; `€` is
the one that falls through.

Control: the same markup on a page declared `charset=utf-8` is requested as
`?a=%e2%82%ac&b=%e2%82%ac&c=%c3%a9&d=x`, which is correct.

## What should happen

Follow the URL Standard's "percent-encode after encoding": a code point that the
page's encoding cannot represent is written as `%26%23`, the code point in
decimal, then `%3B`. Code points the encoding can represent keep going out as
their encoded bytes, as today. Only links' query strings are concerned.

Expected requests (each query must also parse into exactly 4 parameters on the
server):

| page charset | link query in the page | request query |
|---|---|---|
| none (iso-8859-1 fallback) | `?a=&euro;&b=&#8364;&c=&eacute;&d=x` | `a=%26%238364%3B&b=%26%238364%3B&c=%e9&d=x` |
| `utf-8` | same | `a=%e2%82%ac&b=%e2%82%ac&c=%c3%a9&d=x` (unchanged) |
| `shift_jis` | `?a=&#12354;&c=&#12316;&b=&euro;&d=x` | `` a=%82%a0&c=%81`&b=%26%238364%3B&d=x `` (the `` ` `` is the second byte of 〜; あ and 〜 are representable, € is not) |
| `iso-2022-jp` | same as shift_jis | `a=` and `c=` carry the iso-2022-jp bytes (they start with `%1b$B`) and must **not** be escaped; `b=%26%238364%3B`; `d=x` |

Note the iso-2022-jp case: a stateful encoding represents あ and 〜 although the
encoded bytes do not round-trip byte by byte, so "can this be represented" must
be decided by whether the conversion succeeds, not by comparing a round trip.
A conversion that would silently substitute a replacement character (some
platforms' converters do that instead of failing) must count as "cannot be
represented" for this purpose.
