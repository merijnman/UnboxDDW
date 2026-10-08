"""Audio mixdown: schedule.json -> build/mix.wav (48 kHz stereo).

Places voice clips and sounds at their scheduled times on top of the looped
bed. Still to come in T15: ducking the bed under speech, crossfaded loop
points and loudness normalisation. Voice fx (T13) are not applied yet.
"""

from __future__ import annotations

from pathlib import Path

from .audio import CHANNELS, SAMPLE_RATE, run_ffmpeg

PACED_FADE = 0.05


def build_mix(schedule: dict, sounds: dict, build_dir: Path, out: Path) -> Path:
    duration = schedule["duration"]
    inputs: list[str] = ["-f", "lavfi", "-t", f"{duration:.3f}", "-i", f"anullsrc=r={SAMPLE_RATE}:cl=stereo"]
    chains: list[str] = []
    count = 1  # input 0 is the silent base that fixes the length
    for name in schedule["bed"]:
        info = sounds.get(name)
        if not info:
            continue
        inputs.extend(["-stream_loop", "-1", "-i", info["wav"]])
        chains.append(f"[{count}:a]atrim=0:{duration:.3f},volume={schedule['bed_gain_db']}dB[b{count}]")
        count += 1

    for event in schedule["events"]:
        kind = event["type"]
        if kind == "voice":
            inputs.extend(["-i", event["wav"]])
            filters = []
            if event.get("trim"):
                length = event["end"] - event["t"]
                filters += [f"atrim=0:{length:.3f}", f"afade=t=out:st={length - PACED_FADE:.3f}:d={PACED_FADE}"]
        elif kind == "sound" and not event.get("missing"):
            info = sounds[event["name"]]
            inputs.extend(["-i", info["wav"]])
            length = event["end"] - event["t"]
            filters = [f"atrim=0:{length:.3f}"]
            if info["gain_db"]:
                filters.append(f"volume={info['gain_db']}dB")
            if info["fade_in"]:
                filters.append(f"afade=t=in:st=0:d={info['fade_in']}")
            if info["fade_out"]:
                fade = min(info["fade_out"], length)
                filters.append(f"afade=t=out:st={length - fade:.3f}:d={fade}")
        else:
            continue
        delay = round(event["t"] * 1000)
        filters.append(f"adelay={delay}|{delay}")
        chains.append(f"[{count}:a]{','.join(filters)}[b{count}]")
        count += 1

    mix_inputs = "[0:a]" + "".join(f"[b{i}]" for i in range(1, count))
    graph = ";".join(chains + [f"{mix_inputs}amix=inputs={count}:normalize=0:duration=first[out]"])
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.stem + ".tmp.wav")
    graph_file = build_dir / "mix_graph.txt"
    graph_file.write_text(graph, encoding="utf-8")
    run_ffmpeg(
        [*inputs, "-/filter_complex", graph_file.name, "-map", "[out]",
         "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS), "-c:a", "pcm_s16le",
         "-t", f"{duration:.3f}", tmp.relative_to(build_dir).as_posix()],
        cwd=build_dir,
    )
    tmp.replace(out)
    return out
