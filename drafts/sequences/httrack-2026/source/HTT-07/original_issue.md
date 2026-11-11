A PROPFIND on an exact cache entry crashes proxytrack
`PT_Enumerate()` reports the folder's default document as a zero-length name. The listing loop in `proxytrack_process_DAV_Request()` then evaluates `thisUrl[thisUrlLen - 1]` with an unsigned length of 0, which reads 4 GB past the string, and the process dies. It takes the whole listener down rather than the one connection, so a single unauthenticated request is enough.

Reproduced on 536d751 against a one-entry ARC cache holding `http://example.com/page.html`:

```
curl -X PROPFIND -H 'Depth: 1' http://127.0.0.1:$PORT/webdav/example.com/page.html
```

```
#0  proxytrack_process_DAV_Request (...) at src/proxy/proxytrack.c:750
#1  proxytrack_process_HTTP (...) at src/proxy/proxytrack.c:1140
```

The `/*isDefault */ (thisUrlLen == 0)` argument a few lines below shows the empty name is expected there; only the `thisIsDir` test forgot about it.

The same idiom is at line 857 in `proxytrack_process_HTTP_List()`, unreachable today because `listRequest` is never set to 1.

Found while sweeping #821.

