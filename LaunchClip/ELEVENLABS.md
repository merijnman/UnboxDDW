# ElevenLabs: how we use it

Checked against the ElevenLabs API reference on 8 October 2026. Model names and
plan limits change; re-check before the first paid run.

## 1. Setup

1. Create an API key in the ElevenLabs dashboard (Developers, API keys).
2. Put it in `.env` as `ELEVENLABS_API_KEY=...`. The file is in `.gitignore`.
3. Every request sends the header `xi-api-key: <key>`.

Volume: one full render of the show is about 2,900 characters of speech, the
pre-show chatter about 370. Thanks to the cache you only pay again for turns
whose text or settings changed. Sound effects are billed separately per generation.

## 2. Speech

Endpoint

```
POST https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps?output_format=mp3_44100_128
```

Body, one request per `say` entry. `text` is the entry's `en` lines joined with
a single space.

```json
{
  "text": "Welcome, crew. My name is Commander Lockton, but you can call me Dan. I will be your mission commander. In fifteen minutes, we launch to explore metaphorical space!",
  "model_id": "eleven_multilingual_v2",
  "voice_settings": {
    "stability": 0.45,
    "similarity_boost": 0.75,
    "style": 0.35,
    "use_speaker_boost": true,
    "speed": 0.95
  },
  "seed": 1101
}
```

Response

```json
{
  "audio_base64": "...",
  "alignment": {
    "characters": ["W", "e", "l", "..."],
    "character_start_times_seconds": [0.0, 0.07, 0.12],
    "character_end_times_seconds": [0.07, 0.12, 0.19]
  },
  "normalized_alignment": { "...": "same shape, for the normalised text" }
}
```

What the code does with it

- Decode `audio_base64` to `build/cache/tts/<hash>.mp3`, save `alignment` next to it.
- Use `alignment` (original text), not `normalized_alignment`, so character
  positions match our own string.
- Subtitle line N starts at `character_start_times_seconds[offset of its first character]`
  and ends at the end time of its last character plus 0.3 s, capped at the next line's start.
- Clip duration = last `character_end_times_seconds`, cross-checked with ffprobe.

Cache key: SHA-256 of `text`, `voice_id`, `model_id`, `voice_settings`, `seed`
and `output_format`. `seed` makes repeats likely to match but ElevenLabs does not
guarantee it; the cache is what makes reuse certain. A reroll is the same request
with a different seed.

Not used on purpose: `previous_text` and `next_text`. They improve continuity
between consecutive requests, but then a text change in one turn would
invalidate its neighbours. Our turns are separated by pauses anyway.

Format: `mp3_44100_128` is the default and available on every plan (192 kbps mp3
needs Creator or higher, 44.1 kHz PCM needs Pro or higher). We convert to
48 kHz wav locally for mixing; the difference is not audible through a PA.

## 3. Voices and direction

Two voices that differ in pitch and in manner, so the audience can tell who is
speaking even without the label in the subtitle.

| | Commander Lockton ("Dan") | Mission Control |
|---|---|---|
| Character | Warm, confident, dry humour. Talks to people. | Clipped, procedural, never surprised. Reads checklists. |
| Look for in the voice library | Middle-aged, British or mid-Atlantic, narration or character voice | Neutral American, news or announcer voice, contrasting gender or pitch |
| stability | 0.45 (more expression) | 0.70 (flat and consistent) |
| style | 0.35 | 0.10 |
| speed | 0.95 | 1.00 |
| fx afterwards | `radio_light` | `radio` |

Shortlist three candidates per role in the voice library, then run
`unbox audition`. Audition passage, chosen because it contains a long sentence,
a joke, a short exclamation and a number:

- Commander: "Welcome, crew. In fifteen minutes, we launch to explore metaphorical space! It's not rocket science. Yikes."
- Mission Control: "Mission Control here. All systems nominal. Alert! Alert! T minus ten seconds."

Pick on three things: intelligible over background noise, the two are clearly
different, and the short lines do not sound silly.

## 4. Writing text for the voice

The `en` line is both the subtitle and the instruction to the voice.

- Write numbers as words ("fifteen", "thirty seconds"). Digits are read inconsistently.
- Punctuation is the pacing tool. A full stop gives a real pause, a comma a short
  one, a colon a beat before the point ("So: food!").
- An exclamation mark raises energy. Use it once per turn at most.
- Keep sound words like "Ahem" in the text; the voice performs them reasonably.
- No stage directions in the text. With `eleven_multilingual_v2`, "[mumbling]"
  would be read aloud. Directions live in `delivery:` and are done with fx.
- Names: listen to how "Lockton" and "Unbox" come out. If wrong, respell
  phonetically in a `say_as:` field (to add in the model when needed) while the
  subtitle keeps the real spelling.

Special cases

- **The aside ("Brave idiots.")** is its own entry so it can get the `aside` fx:
  quieter, duller, as if off-mic. Expect to reroll it a few times.
- **The countdown** uses `pace_seconds: 1.0`. Each line is a separate request and
  is placed on a one-second grid. Clips longer than 0.9 s are trimmed with a short fade.
- **"Yikes."** One-word requests are the least predictable. If no take works, try
  "Oh. Yikes." or render it together with the next sentence and keep it as one turn.

## 5. Sound effects

Endpoint

```
POST https://api.elevenlabs.io/v1/sound-generation
```

```json
{
  "text": "Spaceship red alert klaxon, urgent repeating alarm",
  "duration_seconds": 7,
  "prompt_influence": 0.5,
  "loop": false,
  "model_id": "eleven_text_to_sound_v2"
}
```

The response is the mp3 itself. Facts that shape our design:

- `duration_seconds` is between 0.5 and 30. So the 25-minute ambience is a loop
  of short pieces, never one long generation.
- `loop: true` asks for a seamlessly looping result. Use it for `bridge_hum` and
  `console_beeps`. Two layers with different lengths (30 s and 22 s) only line up
  again after 5.5 minutes, which hides the repetition.
- `prompt_influence` defaults to 0.3. We use 0.5: closer to the prompt, less variation.
- No seed. Every call gives a new sound, so listen, keep the good one, and let
  the cache hold on to it. `unbox sfx --reroll alarm` when you want another take.

Prompts are in `show.yaml` under `sounds`. Describe the sound source, the
character and what must be absent ("no melody, no voices").

Celebration music: either a licensed track dropped into `assets/music/`, or
generated with ElevenLabs Music. Not automated in the first version.

## 6. Radio effect (local, ffmpeg)

Starting points, to tune by ear in T13.

```
radio        highpass=f=350,lowpass=f=3000,acompressor=threshold=-20dB:ratio=8:attack=5:release=80,volume=4dB
radio_light  highpass=f=120,lowpass=f=6500,acompressor=threshold=-18dB:ratio=3:attack=10:release=120
aside        lowpass=f=2500,volume=-9dB
```

`radio` also gets the `radio_crackle` sound, trimmed to 0.25 s, before and after
the turn, and a bed of quiet static underneath while the voice speaks.

## 7. Later experiment: the newer expressive models

ElevenLabs lists `eleven_v3` and `eleven_v4` as more expressive, with audio tags
for delivery (a muttered aside would then be a tag instead of an fx). The docs
describe v4 as available through the Text to Dialogue API, which has its own
with-timestamps endpoint. Not verified for this project: whether seed and
character timestamps behave the same way there. Treat it as a one-session spike
after M2: render `cmd_aside` and `cmd_yikes` on a newer model and compare. Because
the model id is part of the cache key and set per voice in `show.yaml`, switching
later is a config change.

## 8. Pitfalls

- 429 or 5xx: retry with backoff, and keep requests sequential. Concurrency
  limits on the lower plans are small.
- A text change of one comma is a new cache key and a new take that may sound
  different. Fix typos in `nl` freely; touch `en` only when you mean it.
- Do not commit `.env`. Do commit `build/cache/manifest.json` if you want the
  team to see which takes are current; the audio itself is better shared as a zip.

Sources

- https://elevenlabs.io/docs/api-reference/text-to-speech/convert-with-timestamps
- https://elevenlabs.io/docs/api-reference/text-to-sound-effects/convert
- https://elevenlabs.io/docs/overview/models
