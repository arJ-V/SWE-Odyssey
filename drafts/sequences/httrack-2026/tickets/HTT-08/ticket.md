# WebHTTrack serves PNGs as `image/gif`, JPEGs as downloads, and guesses types from anywhere in the path

`htsserver` (the small web server behind WebHTTrack) picks the `Content-type`
of a file it serves from the file name, and gets it wrong in several ways. This
affects both the GUI's own pages (`html/...`) and a mirror browsed through the
GUI (`/website/...`).

Observed, with `htsserver <html dir> --port N`:

- `/img/guide-droid-opt-spider.png` is served as `image/gif`: every PNG is
  labelled as a GIF. Browsers sniff `<img>` so nobody noticed, but anything that
  trusts the header is misled.
- `/images/screenshot_01.jpg` is served as `application/octet-stream`, so a
  browser opening it directly downloads it instead of showing it. Same for
  `.jpeg`, `.svg`, `.ico`, `.webp` and `.xml`.
- The type is chosen because the path merely *contains* an extension somewhere:
  `notes.htm.txt` is served as HTML, `data.js.bin` as JavaScript, a file
  `plain.bin` (or `plain`) inside a directory called `photos.css/` as CSS, and
  `app.jsx` as JavaScript.
- Upper-case extensions (`PROBE.PNG`, `Legacy.HtM`) are not recognised.

## What should happen

The type comes from the **whole extension of the last path segment** (the text
after the last `.` of the file name), compared case-insensitively:

| extension | Content-type |
|---|---|
| `html`, `htm` | `text/html` |
| `css` | `text/css` |
| `js` | `text/javascript` |
| `txt` | `text/plain` |
| `xml` | `application/xml` |
| `gif` | `image/gif` |
| `png` | `image/png` |
| `jpg`, `jpeg` | `image/jpeg` |
| `webp` | `image/webp` |
| `avif` | `image/avif` |
| `svg` | `image/svg+xml` |
| `ico` | `image/x-icon` |

Anything else, including a name without an extension, a dotted directory with
a dotless file name, and extensions that only start like a listed one
(`.jsx`, `.jpgx`, `.icon`), is `application/octet-stream`. For example
`com.httrack.WebHTTrack.metainfo.xml` is `application/xml`, and
`notes.htm.txt` is `text/plain`.

Keep everything else about the responses as it is: HTML (GUI pages and mirrored
`.html`/`.htm` pages alike) keeps its `Cache-Control: no-cache, ...` and
`Pragma: no-cache` headers; a mirrored HTML page is still streamed verbatim
(`${...}` placeholders in it are not expanded); the status line and every header
line end with CRLF (for example the raw response to
`GET /img/guide-droid-opt-spider.png HTTP/1.0` starts with
`HTTP/1.0 200 OK\r\n` and contains `\r\nContent-type: image/png\r\n`, with no
bare LF in the header block); and the body is sent as before.
