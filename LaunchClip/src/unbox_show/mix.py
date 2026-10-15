"""Audio mixdown: schedule.json -> build/mix.wav (48 kHz stereo).

Every clip is first brought to a reference loudness, so voices and effects sit
at predictable levels whatever ElevenLabs delivered. Then:

- voices get their fx (radio, radio_light, aside); radio turns, and voices
  with `squelch: true`, get a burst of radio crackle before and a click after;
- beds loop under the whole show, sounds play at their times;
- everything that is not a voice is ducked while someone speaks;
- the master is lifted so voices sit at -16 LUFS, with peaks limited to -1.5 dB.

with_fx=False gives the clean mix: raw voices, no effects, no squelch.
"""

from __future__ import annotations

from pathlib import Path

from . import fx
from .audio import CHANNELS, SAMPLE_RATE, run_ffmpeg
from .cache import Cache

VOICE_LUFS = -18.0
SOUND_LUFS = -20.0   # reference for sounds and beds before their gain_db
TARGET_LUFS = -16.0
TRUE_PEAK = -1.5
ASIDE_DB = -6.0      # an aside is spoken off-mic, so it stays quieter
PACED_FADE = 0.05
SQUELCH_LEAD = 0.7      # crackle before a radio turn
SQUELCH_LEAD_DB = -4.0
SQUELCH_SECONDS = 0.5   # crackle after it, as loud as the lead
SQUELCH_TAIL_DB = -4.0
DUCK_RAMP_IN, DUCK_RAMP_OUT = 0.15, 0.4
LOOP_XFADE = 2.0     # a sound placed for longer than its take repeats with this crossfade


def _speech_intervals(events: list[dict]) -> list[tuple[float, float]]:
    spans = sorted((e["t"], e["end"]) for e in events if e["type"] == "voice")
    merged: list[list[float]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1] + 0.5:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(a, b) for a, b in merged]


def duck_expression(intervals: list[tuple[float, float]], duck_db: float) -> str:
    """volume= expression: 1 outside speech, duck_db inside, with short linear ramps."""
    if not intervals:
        return "1"
    depth = 1 - 10 ** (duck_db / 20)
    terms = [
        f"clip(min((t-{a - DUCK_RAMP_IN:.3f})/{DUCK_RAMP_IN},({b + DUCK_RAMP_OUT:.3f}-t)/{DUCK_RAMP_OUT}),0,1)"
        for a, b in intervals
    ]
    inside = terms[0]
    for term in terms[1:]:
        inside = f"max({inside},{term})"
    return f"1-{depth:.4f}*{inside}"


def envelope_expression(points: list[list[float]]) -> str:
    """volume= expression for [show time, dB] points: flat before the first and after the last, straight lines between."""
    expr = f"{points[-1][1]}"
    for (t0, db0), (t1, db1) in reversed(list(zip(points, points[1:]))):
        slope = (db1 - db0) / (t1 - t0) if t1 > t0 else 0.0
        expr = f"if(lt(t,{t1:.3f}),{db0}+{slope:.6f}*(t-{t0:.3f}),{expr})"
    return f"pow(10,(if(lt(t,{points[0][0]:.3f}),{points[0][1]},{expr}))/20)"


def loop_starts(length: float, take: float, piece: float) -> list[float]:
    """Offsets of the repeats that fill `length`. One start when the take is long enough."""
    starts = [0.0]
    if length > take + 1e-6:
        while starts[-1] + piece < length:
            starts.append(starts[-1] + piece - LOOP_XFADE)
    return starts


def _rel(path: Path, build_dir: Path) -> str:
    return path.resolve().relative_to(build_dir.resolve()).as_posix()


def build_mix(schedule: dict, sounds: dict, build_dir: Path, out: Path, show=None, cache: Cache | None = None,
              with_fx: bool = True) -> Path:
    duration = schedule["duration"]
    cache = cache or Cache(build_dir / "cache")
    inputs: list[str] = ["-f", "lavfi", "-t", f"{duration:.3f}", "-i", f"anullsrc=r={SAMPLE_RATE}:cl=stereo"]
    chains: list[str] = []
    voices: list[str] = []
    others: list[str] = []
    count = 1  # input 0 is the silent base that fixes the length

    def add(path: Path, filters: list[str], bus: list[str], loop: bool = False) -> None:
        nonlocal count
        if loop:
            inputs.extend(["-stream_loop", "-1"])
        inputs.extend(["-i", _rel(path, build_dir)])
        chains.append(f"[{count}:a]{','.join(filters)}[a{count}]")
        bus.append(f"[a{count}]")
        count += 1

    def gain(path: Path, reference: float, extra: float = 0.0) -> str:
        return f"volume={reference - fx.loudness(path, cache) + extra:.2f}dB"

    def delay(t: float) -> str:
        ms = max(0, round(t * 1000))
        return f"adelay={ms}|{ms}"

    for name in schedule["bed"]:
        info = sounds.get(name)
        if info:
            wav = build_dir / info["wav"]
            filters = [f"atrim=0:{duration:.3f}"] + ([info["filter"]] if info.get("filter") else [])
            add(wav, filters + [gain(wav, SOUND_LUFS, schedule["bed_gain_db"])], others, loop=True)

    crackle = sounds.get("radio_crackle")
    first_last: dict[str, list[float]] = {}
    for event in schedule["events"]:
        if event["type"] == "voice":
            names = []
            if with_fx and event.get("fx"):
                names.append(event["fx"])
            if with_fx and event.get("aside"):
                names.append("aside")
            wav = fx.apply(build_dir / event["wav"], names, show, cache) if names else build_dir / event["wav"]
            # Measured after the effect, which changes loudness, so both voices end up equally loud.
            filters = [gain(wav, VOICE_LUFS, ASIDE_DB if event.get("aside") else 0.0)]
            if event.get("trim"):
                length = event["end"] - event["t"]
                filters += [f"atrim=0:{length:.3f}", f"afade=t=out:st={length - PACED_FADE:.3f}:d={PACED_FADE}"]
            add(wav, filters + [delay(event["t"])], voices)
            if event.get("fx") == "radio" or event.get("squelch"):
                span = first_last.setdefault(event["entry"], [event["t"], event["end"]])
                span[1] = max(span[1], event["end"])
        elif event["type"] == "sound" and not event.get("missing"):
            info = sounds[event["name"]]
            wav = build_dir / info["wav"]
            length = event["end"] - event["t"]
            piece = min(info.get("loop_length") or info["duration"], info["duration"])
            starts = loop_starts(length, info["duration"], piece)
            envelope = event.get("envelope")
            for k, offset in enumerate(starts):
                last = k == len(starts) - 1
                seg = length - offset if last else piece
                filters = [f"atrim=0:{seg:.3f}"] + ([info["filter"]] if info.get("filter") else [])
                filters.append(gain(wav, SOUND_LUFS, info["gain_db"]))
                if k:
                    filters.append(f"afade=t=in:st=0:d={LOOP_XFADE}")
                elif info["fade_in"]:
                    filters.append(f"afade=t=in:st=0:d={info['fade_in']}")
                if not last:
                    filters.append(f"afade=t=out:st={seg - LOOP_XFADE:.3f}:d={LOOP_XFADE}")
                elif info["fade_out"]:
                    fade = min(info["fade_out"], seg)
                    filters.append(f"afade=t=out:st={seg - fade:.3f}:d={fade}")
                filters.append(delay(event["t"] + offset))
                if envelope:  # after the delay, so t is show time
                    filters.append(f"volume=eval=frame:volume='{envelope_expression(envelope)}'")
                add(wav, filters, others)

    if with_fx and crackle:
        wav = build_dir / crackle["wav"]
        for start, end in first_last.values():
            for t, length, level in ((start - SQUELCH_LEAD, SQUELCH_LEAD, SQUELCH_LEAD_DB),
                                     (end, SQUELCH_SECONDS, SQUELCH_TAIL_DB)):
                filters = [f"atrim=0:{length}", "lowpass=f=4000:poles=2",
                           f"afade=t=in:st=0:d=0.03", f"afade=t=out:st={length - 0.08:.2f}:d=0.08",
                           gain(wav, SOUND_LUFS, level), delay(t)]
                add(wav, filters, others)

    graph = list(chains)
    graph.append("[0:a]" + "".join(voices) + f"amix=inputs={len(voices) + 1}:normalize=0:duration=first[voices]")
    duck = duck_expression(_speech_intervals(schedule["events"]), schedule["duck_bed_under_speech_db"])
    if others:
        graph.append("".join(others) + f"amix=inputs={len(others)}:normalize=0[rest]")
        graph.append(f"[rest]volume=eval=frame:volume='{duck}'[ducked]")
        graph.append("[voices][ducked]amix=inputs=2:normalize=0:duration=first[out]")
    else:
        graph.append("[voices]anull[out]")

    build_dir.mkdir(parents=True, exist_ok=True)
    graph_file = build_dir / "mix_graph.txt"
    graph_file.write_text(";\n".join(graph), encoding="utf-8")
    premix = build_dir / "premix.wav"
    run_ffmpeg(
        [*inputs, "-/filter_complex", graph_file.name, "-map", "[out]",
         "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS), "-c:a", "pcm_f32le",
         "-t", f"{duration:.3f}", premix.name],
        cwd=build_dir,
    )
    return master(premix, out)


def master(src: Path, out: Path) -> Path:
    """Lift the premix so the voices land at TARGET_LUFS, then limit peaks.

    Not an integrated-loudness normalisation: most of the show is quiet ambience
    while the groups talk, and that should stay quiet."""
    ceiling = 10 ** (TRUE_PEAK / 20)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.stem + ".tmp.wav")
    run_ffmpeg(["-i", str(src), "-af", f"volume={TARGET_LUFS - VOICE_LUFS:.2f}dB,alimiter=limit={ceiling:.4f}:level=false",
                "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS), "-c:a", "pcm_s16le", str(tmp)])
    tmp.replace(out)
    return out
