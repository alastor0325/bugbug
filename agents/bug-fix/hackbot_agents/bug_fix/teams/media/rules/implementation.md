## Read first

Before you change media code, read the design doc for that area in `dom/media/docs/`:

| Code you change                                | Doc to read                           |
| ---------------------------------------------- | ------------------------------------- |
| `dom/media/MediaFormatReader.*`, demux, decode | `dom/media/docs/MediaFormatReader.md` |
| `dom/media/AudioStream.*`, cubeb output        | `dom/media/docs/AudioStream.md`       |
| `dom/media/mediasink/` (audio sink, clock)     | `dom/media/docs/AudioSinkWrapper.md`  |
| `dom/media/webspeech/`                         | `dom/media/docs/SpeechRecognition.md` |

Then read the review checklists in `dom/media/docs/review/` that fit the change
(threading, async lifecycle, IPC, memory safety, spec correctness). Use them while you
write the fix, not only when it is reviewed.

## Find the cause

- Fix the cause, not the symptom. If an assertion breaks, find why the state became
  reachable. Do not add a guard that tolerates it.
- For web-observable behaviour, follow the spec steps. Fix the code where Firefox departs
  from the spec, and keep the assertion.
- If no real flow reaches a condition, assert it with `MOZ_ASSERT`. Do not add a silent
  clamp or a warning log.

## Media code

- Threads: most media objects belong to one thread or task queue and assert it, for
  example `MOZ_ASSERT(OnTaskQueue())` in `MediaDecoderStateMachine` and
  `MediaFormatReader`. Keep the change on that thread. To reach another thread,
  dispatch a task or chain a `MozPromise`. Do not add a lock.
- Async work: a promise callback or a dispatched task can run after shutdown. Disconnect
  each `MozPromiseRequestHolder` on shutdown, and check the shutdown state before you
  use the object.
- Processes: decoders run in the RDD process, and CDMs in the GMP or media engine
  processes (`dom/media/ipc/`, `dom/media/gmp/`). Do not trust a size, an index or a
  state that comes from another process. Check it where it arrives.
- Platforms: code in `dom/media/platforms/wmf`, `apple`, `ffmpeg` and `android` builds
  only on that platform.
- Logging: log with the module and the macro that the file already uses, for example
  `gMediaDecoderLog` or `gAudioSinkLog`. Log each new state change. Do not log a value
  that is already logged nearby.
- Prefs: `media.*` prefs are in `modules/libpref/init/StaticPrefList.yaml`. A test sets
  a pref with `SpecialPowers.pushPrefEnv`, or in its test manifest.

## Test first

- If the new test does not fail on the unfixed tree, change the test.
- If the bug has a testcase or a media file attached, test with it.
- Drive the test through real user paths and assert the exact observable value. Do not
  call internal APIs, and do not use a tolerance or a mean.
- Put the test where the media team keeps tests of its kind:

| Test of                              | Where                                                            |
| ------------------------------------ | ---------------------------------------------------------------- |
| Media element and playback behaviour | mochitest in `dom/media/test/`; media files are in `manifest.js` |
| A crash                              | crashtest in `dom/media/test/crashtests/`                        |
| C++ internals                        | gtest in `dom/media/gtest/`                                      |
| Media Source Extensions              | `dom/media/mediasource/test/`, `dom/media/mediasource/gtest/`    |
| Web Audio                            | `dom/media/webaudio/test/`                                       |
| WebCodecs                            | `dom/media/webcodecs/test/`, WPT `webcodecs/`                    |
| Spec behaviour of media elements     | WPT `html/semantics/embedded-content/media-elements/`            |

## Comments

- Write few comments: only for logic that is not obvious and for the intent of a class or
  member. Never repeat what the code says.
- A comment says what the code means. It does not name callers, the call order or the
  history of the patch.
- Do not add, change or remove comments on lines the patch does not change.

## Scope of the diff

- Change only what the fix needs.
- Do not move an existing function. Change it where it is.
- Commit the test after the fix, as its own commit. The revision can hold both commits.

## Commit message

- Write it by the rules in `.claude/skills/firefox-commits/SKILL.md` in the source tree.
  Leave the reviewer out of the title: the person who takes the patch picks one.
- Never use `--` or an em dash as an aside. Use a comma, a colon or two sentences.
