# Diagnostics: the media team

Applies with the general `diagnostics.md`, in `workflow.md` steps 2a, 2b and 5. The media
facts are in the in-tree page `dom/media/docs/Diagnostics.md`, which the system prompt
carries: the about:support sections and prefs, the media log signals, the media profile
threads, the hardware decode failure pattern, what a useful report has, and the capture
steps. This file adds only the triage decisions.

## Prefs

Read the prefs in "Important Modified Preferences", as the in-tree page says. A pref
that turns off hardware decoding, the GPU, a decoder or a CDM can explain the bug. Then
make the pref reset the primary ask in "Needs info". Ask the reporter to reset those
prefs, close every Firefox window and restart. The restart is not optional. The team's
`comment.md` says where the capture goes.

## Profiles

A profile link by itself does not prove that a media bug has enough data. A profile
without the media threads of the in-tree page does not count as evidence for "Analyzed".
Record it as present but non-media, and add "media profile" to what is missing when the
bug type needs it (bug 2040168 is an example). The bug can still go to "Analyzed" through
another item in the decision rule.

The `about:logging` route in the capture block is a different capture path. It often
works where the profiler UI failed (bug 2071427).

When a profile shows the hardware decode failure pattern of the in-tree page, check its
three causes. When the capability API also reports support while new decoders fail, the
bug is "Analyzed" with two problems. One is the hardware failure. The other is that the
capability API does not show runtime availability.

## Media logs

A media log inline in a comment: read it for the signals of the in-tree page, and give
the findings in the inventory. A media log sent by email to media-alerts@mozilla.com:
record it as present and not readable by the agent. Do not ask the reporter to send it
again by email.

## Required information

"What a useful report has" in the in-tree page lists the data for each media bug type.

**Decision rule.** After step 2b, use the artifact findings, not only the presence of an
item, to decide whether to spawn `bug_investigator`. The investigation needs enough data
to start, not a specific artifact. Any one of these is enough:

- a media profile;
- a media log with an `NS_ERROR` or decoder signal;
- a crash id with a media frame;
- steps that reproduce the problem every time;
- a cause that the reporter found, with source citations;
- a codec or configuration with a clear cross-browser failure pattern.

Performance and audio-quality bugs are the exception: they need the `about:logging`
capture, because steps alone do not show the cause. When one item is present, missing
items do not block the investigation. Spawn `bug_investigator`, then use its "Fixable"
answer and the Fixability table in `workflow.md` to choose the branch. The table also
covers the expert-led answer. "The decision" in `workflow.md` then decides between
`analyzed` and `needs_info`.
