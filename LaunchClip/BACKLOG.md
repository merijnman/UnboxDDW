# Unbox show video: backlog

Goal: turn `show.yaml` into finished video files that anyone can play on a laptop
with a beamer. No Python on site. If the script changes, we edit `show.yaml`,
run one command and get a new video. Anything that did not change (a voice
turn, a sound effect, a screen) comes from the cache.

## 1. What we deliver

| File | What | Length |
|---|---|---|
| `dist/unbox_show.mp4` | The session: voices, sounds, screens, timers, EN + NL subtitles | 15:00 |
| `dist/unbox_preshow.mp4` | Between sessions: bridge ambience, radio chatter, "next launch in" clock | 25:00 |
| `dist/unbox_loop.mp4` | Pre-show followed by show, one file to loop all day (optional) | 40:00 |
| `dist/run_sheet.html` | Printable cue list for the people in the room (smoke machine, curtain, lights) | 1 page |
| `dist/transcript_nl.html` | Printable Dutch transcript card for the tables | 1-2 pages |

Video: H.264, 1920x1080, 25 fps, AAC stereo 48 kHz. Plays in VLC and Windows
Media Player without extra codecs. Subtitles are burned into the picture.

## 2. Architecture

One Python package, one CLI, ffmpeg for all audio and video work.

```
show.yaml ──> validate ──> tts ──┐
                           sfx ──┼─> schedule ──> subtitles ──┐
                                 │        │                    ├─> video ──> dist/*.mp4
                                 │        ├────> audio mix ────┤
                                 │        └────> frames ───────┘
                                 └─> build/cache/  (content-addressed, never deleted by a build)
```

Principles

- `show.yaml` is the only place content lives. No text, timing or file name in code.
- Each stage is a pure function from files to files. Each stage has its own CLI
  command, so a coding agent can build and test one stage per session.
- Cache key = SHA-256 of everything that influences the result (text, voice id,
  model, settings, seed, fx chain). Same key = reuse the file, zero API calls.
- All ElevenLabs calls go through one small client class with a fake twin. Tests
  and the first milestone never touch the network.
- The scheduler is the judge. It works with measured audio durations and fails
  the build if two voice turns overlap or a turn runs past the next cue.

Layout. LaunchClip/ is a subfolder of the UnboxDDW repo, next to Projection/.
The git repo (`.git`) lives at the UnboxDDW root, so do not run `git init` here.

```
LaunchClip/
  show.yaml
  .env                      # ELEVENLABS_API_KEY=...   (never committed)
  assets/screens/           # real artwork, same file names as in show.yaml
  assets/music/
  assets/fonts/
  src/unbox_show/
    cli.py                  # unbox validate | plan | tts | sfx | build | handouts
    model.py                # pydantic models for show.yaml
    plan.py                 # estimated durations, dry-run table
    eleven.py               # ElevenClient + FakeElevenClient
    cache.py                # hashing, lookup, manifest
    tts.py  sfx.py  fx.py
    schedule.py             # resolves the timeline to absolute events
    subtitles.py            # .ass (burn-in) and .srt
    mix.py                  # audio mixdown
    frames.py               # Pillow renderer for screens, overlays, timers
    video.py                # ffmpeg assembly
    handouts.py
  tests/
  build/                    # cache/, schedule.json, mix.wav, frames/, subs.ass
  dist/
```

Dependencies: Python 3.11+, `pydantic`, `pyyaml`, `httpx`, `pillow`, `typer`,
`pytest`. ffmpeg on PATH (`winget install Gyan.FFmpeg`). No moviepy, no ElevenLabs
SDK: two HTTP endpoints are less code than a dependency. Reuse the `.env` and
config conventions from the earlier prototypes (playsafe, offertehulp) where they fit.

## 3. Milestones

- **M1 Silent animatic.** Complete 15-minute video with screens, timers and both
  subtitle tracks, with silence where the voices will be. No API key, no cost.
  Good enough to review the pacing with the team.
- **M2 Voices.** Real ElevenLabs voices, subtitles synced to the actual speech.
- **M3 Sound.** Ambient bed, radio effect, alarm, launch, music, mixed and levelled.
- **M4 Everything around it.** Pre-show video, loop file, run sheet, transcript card.
- **M5 Polish.** Real artwork, house style, test on the venue hardware.

## 4. Tasks

Each task is sized for one coding-agent session. "Done when" is the acceptance
test. Tests use `FakeElevenClient` unless stated otherwise.

### M1 Silent animatic

**T01 Scaffold.** `pyproject.toml`, folder layout, `.env.example`,
`LaunchClip/.gitignore` (`build/`, `dist/`; `.env` is already ignored repo-wide
by the root `.gitignore`), README with install steps, `unbox --help`, a startup
check that ffmpeg is on PATH with a clear message if not.
Done when: `pytest` runs (one smoke test) and `unbox --help` lists the commands.

**T02 Model and validation.** Pydantic models for everything in `show.yaml`.
`unbox validate` reports, with the entry id and time: unknown speaker, screen,
overlay or sound; a line without `nl`; `at` not ascending; duplicate ids; a `say`
entry without id; voice ids still `TODO_...` (warning, not error).
Done when: the current `show.yaml` validates and five deliberately broken
fixtures each give the right message.

**T03 Dry-run plan.** `unbox plan` prints a table per entry: start, speaker,
words, estimated duration (words / 2.5 per second, divided by speed, plus 0.35 s
per line break), estimated end, gap to next entry. Flags overlaps and gaps under
1 s. Prints the total character count per voice.
Done when: output matches the reference table in section 6 to within 0.5 s.

**T04 Client and fake.** `ElevenClient` with `tts(text, voice) -> (mp3_bytes, alignment)`
and `sfx(prompt, seconds, loop) -> mp3_bytes`. `FakeElevenClient` returns silence
of the estimated duration and a linear character alignment. Selected by
`--fake` or when no API key is set.
Done when: unit tests cover both methods of the fake, and the real client has a
test with a mocked HTTP transport that checks URL, headers and body.

**T05 Cache.** `cache.key(dict) -> hash`, `cache.get/put`, and
`build/cache/manifest.json` mapping entry id to hash, duration, characters, date.
Done when: a second run makes zero client calls; changing one word in one entry
makes exactly one.

**T06 Scheduler.** Reads the timeline plus measured durations and alignments,
writes `build/schedule.json`: a flat, time-sorted list of events (voice clip
start/end, subtitle line start/end in both languages, screen, overlay, big text,
sound, timer start/end, ops). Line start = time of the line's first character in
the alignment. Line actions fire at the line start. Errors: voice overlap, turn
ending after the next `at`, show longer than `duration`. For `pace_seconds`
entries, every line is its own clip on a fixed grid.
Done when: tests cover a normal turn, an overlap error, a paced countdown and a
line action; `schedule.json` for the full show is committed as a snapshot test.

**T07 Subtitles.** From `schedule.json` write `build/subs.ass` with two styles:
EN (larger, white) and NL directly below it (slightly smaller, light yellow),
both with a dark box, bottom-centred, max two rows each (split long lines at a
comma or the middle space and split the time proportionally). Speaker label in
front of the first line of a turn. `delivery: aside` in italics. Also write
`dist/unbox_show.en.srt` and `.nl.srt`.
Done when: tests check timing, the two-row limit, and that no two events of the
same language overlap.

**T08 Frame renderer.** Pillow function `render(state) -> PNG` where state =
screen, overlays, big text, timer value, ops marker. If an image file is missing,
draw a placeholder (dark background, file name, the `text_en`/`text_nl` of the
screen). Timer as `MM:SS` top right, turning red in the last 10 s. Subtitle area
at the bottom stays empty.
Done when: golden-image tests for six representative states pass, and all
states of the show render without the real artwork.

**T09 Video assembly.** Walk `schedule.json` second by second (plus exact
sub-second event times), render one PNG per distinct state, write an ffmpeg
concat list with durations, burn in `subs.ass`, mux with the audio.
Note for Windows: the `ass=` filter chokes on drive letters and backslashes, so
run ffmpeg with `cwd=build/` and a relative file name.
Done when: `unbox build --fake` produces a 15:00 mp4 (within one frame) whose
frames at 00:40, 01:50, 08:05 and 12:45 match golden images.

**T10 One command.** `unbox build` runs every stage in order and ends with a
report: what was reused, what was regenerated, characters spent, output paths.
`--draft` renders 960x540 for quick checks. `--only show|preshow|loop`.
Done when: a clean checkout plus `unbox build --fake` gives M1 in one go.

### M2 Voices

**T11 Voice audition.** `unbox audition --speaker commander --voices id1,id2,id3`
renders one fixed test passage per candidate into `build/audition/`, with the
settings from `show.yaml`. See `ELEVENLABS.md` for the passage and how to shortlist.
Done when: the command writes one labelled mp3 per voice and the chosen ids are
filled in in `show.yaml`.

**T12 Real TTS.** `unbox tts` renders every `say` entry through the
with-timestamps endpoint, stores mp3 + alignment in the cache, converts to
48 kHz wav for mixing. Retries on 429/5xx with backoff, one request at a time.
`unbox tts --id cmd_aside --reroll` bumps the seed for one entry when a take is bad.
Done when: a full run on the real API fills the cache, a second run is free, and
subtitles in the build follow the real speech.

**T13 Voice effects.** ffmpeg filter chains per `fx` name (`radio`,
`radio_light`, `aside`), applied after TTS, cached under their own key. Squelch
click before and after `radio` turns.
Done when: tests assert the output duration equals input plus squelch, and a
listening check confirms both voices are clearly distinguishable.

### M3 Sound

**T14 Sound effects.** `unbox sfx` generates every entry in `sounds` that has a
`prompt`, caches it, and accepts `file:` entries as they are. `--reroll name`
for a new take.
Done when: all sounds exist in the cache and a missing `file:` gives a clear error.

**T15 Mixdown.** Build `build/mix.wav` from `schedule.json`: looped bed layers
with crossfaded loop points, voice clips at their start times, sounds, music with
fades, bed ducked under speech, final loudness normalised to about -16 LUFS with
a true peak limit of -1.5 dB.
Done when: tests check the duration, that the peak stays under the limit, and
that a known clip sits at the right sample offset.

### M4 Around the show

**T16 Pre-show video.** Render `preshow`: bed, countdown clock, and chatter lines
placed at seeded random gaps (same seed = same video), through the `radio` fx at
low level, no subtitles.
Done when: the file is 25:00 and two builds are byte-identical in the audio schedule.

**T17 Loop file.** Concatenate pre-show and show without re-encoding.
Done when: `dist/unbox_loop.mp4` is 40:00 and the join is seamless in VLC with loop on.

**T18 Handouts.** `unbox handouts` writes the run sheet (time, cue, what the
audience hears just before it) and the Dutch transcript card, as print-ready HTML.
Done when: both files regenerate from `show.yaml` and fit on A4.

### M5 Polish

**T19 Artwork and style.** Drop the real images into `assets/screens/`, add fonts
and colours to a `style:` block in `show.yaml`, tune subtitle size for reading
from the back of the room.
Done when: the team has signed off on a full watch-through.

**T20 Venue test.** Play all files on the actual laptop, beamer and speakers.
Check subtitle legibility, volume of the alarm, and whether the ops marker is
visible to the audience.
Done when: checklist in the README is ticked and a copy of `dist/` is on two USB sticks.

## 5. Decisions already made

- Video, not a live player. Re-rendering is the update mechanism.
- Audio is English, subtitles are English and Dutch, shown together.
- One TTS request per voice turn, not per sentence, so intonation carries
  through. Subtitle timing per line comes from the character timestamps.
- Exception: the countdown. Each number is its own clip on a one-second grid,
  because a TTS voice does not count in strict tempo.
- The radio sound is added afterwards with ffmpeg, not asked from the voice model.
- Speech model: `eleven_multilingual_v2` (stable, supports timestamps). The newer
  expressive models are a later experiment, see `ELEVENLABS.md`.

## 6. Reference timeline (estimates, before real audio)

| Start | Entry | Est. length | Ends |
|---|---|---|---|
| 00:03 | cmd_welcome | 12.5 s | 00:15 |
| 00:18 | cmd_aside | 0.8 s | 00:18 |
| 00:22 | cmd_metaphors | 39.1 s | 01:01 |
| 01:12 | mc_ready | 18.7 s | 01:30 |
| 01:34 | timer 30 s | | 02:04 |
| 02:06 | mc_pick | 12.7 s | 02:18 |
| 02:50 | cmd_share | 14.2 s | 03:04 |
| 03:06 | timer 4:54 | | 08:00 |
| 04:00 | mc_spotlight | 5.2 s | 04:05 |
| 05:00 | mc_nominal | 2.4 s | 05:02 |
| 06:00 | mc_propellant | 2.4 s | 06:02 |
| 07:00 | mc_warning | 16.7 s | 07:16 |
| 08:00 | alarm | 7 s | 08:07 |
| 08:02 | mc_alert | 7.9 s | 08:09 |
| 08:18 | mc_done | 5.2 s | 08:23 |
| 08:25 | cmd_yikes | 0.4 s | 08:25 |
| 08:28 | cmd_select | 17.1 s | 08:45 |
| 08:47 | timer 2:58 | | 11:45 |
| 09:45 | mc_two_minutes | 2.4 s | 09:47 |
| 10:45 | mc_one_minute | 2.4 s | 10:47 |
| 11:46 | cmd_go | 11.2 s | 11:57 |
| 12:30 | mc_all_go | 4.8 s | 12:34 |
| 12:38 | mc_countdown | 15 s | 12:53 |
| 13:15 | celebration music | | 15:00 |
| 13:22 | cmd_outro | 33.0 s | 13:54 |

Spoken English: about 2,900 characters for the show, 370 for the pre-show chatter.

## 7. Risks

- **The alarm speaks over itself.** `mc_alert` starts 2 s into a 7 s alarm. The
  mix must duck the alarm under the voice, or we shorten the alarm to 2 s.
- **Real speech is slower than the estimate.** The commander's pen explanation
  has 11 s of slack; if it runs over, the scheduler stops the build and we move
  `mc_ready` in `show.yaml`.
- **Short lines** ("Yikes.", "Brave idiots.") often sound flat or odd from TTS.
  Expect a few rerolls. That is what `--reroll` is for.
- **Licence.** Check that the ElevenLabs plan in use allows public showing, and
  that the music file may be used.
