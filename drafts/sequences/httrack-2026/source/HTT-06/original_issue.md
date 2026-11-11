ProxyTrack .ndx parse reads an uninitialized offset and bounds a strncat by its source
Two pre-existing problems in `src/proxy/store.c`, found while reviewing #811 and outside that PR's invariant.

`pos` at :1589 is read at :1592 without having been initialized, on the path where the `sscanf` meant to set it fails.

`strncat(cache->filenameDat, filename, nLen)` at :1523 and :1524 bounds the copy by the source length rather than by the 1024-byte destination. The overflow lands inside the same struct, so neither ASan nor _FORTIFY_SOURCE can see it.
