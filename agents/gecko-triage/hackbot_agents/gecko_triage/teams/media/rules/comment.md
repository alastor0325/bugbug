# The comment: the media team

Applies with the general `comment.md`.

## Narrowing questions

At most one, from this table:

| Question                                                               | When                                                  |
| ---------------------------------------------------------------------- | ----------------------------------------------------- |
| Does this happen in Private Browsing mode?                             | Any playback or recording issue; rules out extensions |
| Does this happen on all videos, or only certain ones?                  | Playback failures                                     |
| Does this happen with one codec only (H.264, H.265, AV1, VP9)?         | Playback failures with a codec hint                   |
| Could you please share a public link that shows the problem?           | The reported URL needs a login                        |
| If the content is private, please send it to media-alerts@mozilla.com. | No public repro is possible                           |

## When to ask for the capture block

For audio, video and performance bugs, the media-log capture is the most useful
diagnostic. Make it the first item in "Next steps". The only exception: when
`diagnostics.md` found high-risk `media.*` prefs, the pref reset and restart come first,
and the capture follows, introduced by "If the problem continues after the reset:".

The capture is not a `.moz_log` file, and the reporter does not start it from
profiler.firefox.com.

Write the capture placeholder. The recorder expands it to the team's capture block,
word for word. The system prompt shows that block.

Never retype or shorten the steps, and never put the video link in a reference-style
footnote.

Use the **Graphics** preset (same flow) instead of or in addition to **Media playback**
when the issue is in the GPU, compositor or overlay path rather than playback. Say so in
one sentence before the block; do not edit step 2 of the capture block.
