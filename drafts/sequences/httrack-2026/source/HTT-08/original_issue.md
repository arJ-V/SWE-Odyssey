htsserver labels every PNG as image/gif, and serves JPEG as a download
`htsserver` picks the response content type from the filename in `src/htsserver.c:1462-1476`, and the image branch is wrong twice.

`is_image()` (`:104`) matches `.gif` and `.png`, but the header it selects, `ok_img` (`:992`), hardcodes `Content-type: image/gif`. Every PNG in the doc tree is therefore served labelled as a GIF. Browsers sniff images for `<img>`, so pages still render and nobody has noticed, but the label is a lie and anything that trusts it is misled.

The second half is worse: no other image type is recognised at all. `html/images/screenshot_01.jpg` and `screenshot_01b.jpg` fall through to `application/octet-stream`, which browsers treat as a download rather than an image. The same applies to `.svg`, `.ico` and `.xml` under the served tree.

Worth fixing together: map the extension to its own type instead of picking one header for the whole image class, and cover the types actually present (`gif`, `png`, `jpg`/`jpeg`, `svg`, `ico`, `xml`). Note `is_html()` and the rest use a bare `strstr`, so `.js` matches anywhere in a path; tightening these to a real suffix check would be a reasonable part of the same change.

Found while checking that the new `html/guide.html` and its screenshots are served correctly through WebHTTrack (#874). The CSS and JavaScript branches are fine.

