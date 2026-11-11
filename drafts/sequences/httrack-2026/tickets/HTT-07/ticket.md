# proxytrack dies on a WebDAV PROPFIND for a page that is in the cache

A single WebDAV request kills `proxytrack`: not just the connection, the whole
process, so every other client loses the proxy too. No authentication is
needed.

## How to reproduce

1. Start proxytrack on a one-entry ARC cache that holds
   `http://example.com/page.html` (a `text/html` page):

   ```
   proxytrack 127.0.0.1:$PORT 127.0.0.1:$ICPPORT in.arc
   ```

2. List that page over WebDAV:

   ```
   curl -i -X PROPFIND -H 'Depth: 1' http://127.0.0.1:$PORT/webdav/example.com/page.html
   ```

## What happens

No response; proxytrack has crashed (segmentation fault). Any further request,
for example a `PROPFIND` on `/webdav/example.com/`, gets no answer.

Listing a folder works. The crash only happens when the requested path is
itself an exact cache entry.

## What should happen

`PROPFIND` with `Depth: 1` on `/webdav/example.com/page.html` answers
`207 Multi-Status` with exactly two `<response>` elements: the requested path
itself and its default document. The default document is listed with
`<displayname>Default Document for the Folder</displayname>` and the href of the
collection followed by an empty name, i.e.
`<href>/webdav/example.com/page.html/</href>`. proxytrack stays up, so a
following `PROPFIND` (for example on `/webdav/example.com/`) is answered with
`207` as well.

The plain HTTP directory listing has the same handling of entry names, so it
should not trip over an empty name either.
