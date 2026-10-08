# LaunchClip: the Unbox show video

Turns `show.yaml` into the finished show video (`dist/unbox_show.mp4`) plus EN
and NL subtitle files. Edit `show.yaml`, run one command, get a new video.
Anything that did not change comes from the cache in `build/cache/`.

See `BACKLOG.md` for the plan and `ELEVENLABS.md` for how the voices are made.

## Install

1. Python 3.11 or newer, and [uv](https://docs.astral.sh/uv/).
2. ffmpeg on PATH: `winget install Gyan.FFmpeg`, then open a new terminal.
3. In this folder:

   ```
   uv sync
   uv run unbox --help
   ```

4. Optional, only when you want real voices: copy `.env.example` to `.env`
   and fill in `ELEVENLABS_API_KEY`. Without a key everything runs on the fake
   client: silence of the estimated length, no cost.

## Commands

| Command | What it does | Costs ElevenLabs characters? |
|---|---|---|
| `unbox validate` | Checks `show.yaml`: unknown names, missing translations, ordering, ids | no |
| `unbox plan` | Estimated timing per entry, overlaps, characters per voice | no |
| `unbox audition --speaker commander --voices id1,id2,id3` | One test take per candidate voice in `build/audition/` | yes, after confirmation |
| `unbox tts` | Renders every voice turn into the cache | yes, after confirmation |
| `unbox sfx` | Generates the sound effects into the cache | yes, after confirmation |
| `unbox schedule` | Resolves the timeline into `build/schedule.json` | no |
| `unbox subs` | Writes `build/subs.ass` and `dist/unbox_show.{en,nl}.srt` | no |
| `unbox build` | All of the above, then the video | yes, after confirmation |

Useful flags: `--fake` (never call the API, even with a key), `--draft`
(960x540, quicker), `--yes` (do not ask before spending).

### The budget gate

Before anything is sent to ElevenLabs, the tool lists the requests that are
not cached yet, with their character count, and asks to continue. Cached
takes are free and never asked about. Without a terminal to ask in (scripts,
CI) it stops unless `--yes` is given.

## The workflow

1. **Silent animatic (no key needed).** `uv run unbox build --fake --draft`
   gives a 15-minute video with placeholders, timers and subtitles in about two
   minutes. Use it to review the pacing.
2. **Voice test.** Shortlist three voices per role in the ElevenLabs voice
   library (see `ELEVENLABS.md` section 3), then:

   ```
   uv run unbox audition --speaker commander --voices id1,id2,id3
   uv run unbox audition --speaker mission_control --voices id4,id5,id6
   ```

   That costs about 6 x 100 characters. Listen in `build/audition/`, then put
   the chosen ids in `show.yaml` under `voices`.
3. **Real voices.** Only after the voice test: `uv run unbox build`. The gate
   shows the full cost (about 2,900 characters) before anything is sent.

## What is built so far

- M1 (T01-T10) done: the silent animatic renders end to end.
- T11 (audition) is ready to use, not yet run against the real API.
- Not yet: voice fx (T13), mixdown with ducking and loudness (T15), pre-show
  and loop (T16-T17), handouts (T18). The mix currently places clips and
  sounds without fx or ducking.

## Tests

```
uv run pytest            # fast tests, about 30 s
uv run pytest -m slow    # full 1080p build, about 4 minutes
```

Golden images and the schedule snapshot live in `tests/golden/` and
`tests/snapshots/`. After an intended visual or timing change, re-run with
`UPDATE_GOLDEN=1`, look at the new images, and commit them.
