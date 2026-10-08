"""Voice effects and loudness measurement, all local with ffmpeg.

Effects are applied after TTS and cached under their own key, so tuning a
filter chain never costs an API call.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from . import cache as cache_mod
from .audio import CHANNELS, SAMPLE_RATE, run_ffmpeg
from .cache import Cache, write_bytes_atomic

# Starting points from ELEVENLABS.md section 6; show.yaml `fx:` overrides them.
DEFAULT_CHAINS = {
    "radio": "highpass=f=350,lowpass=f=3000,acompressor=threshold=-20dB:ratio=8:attack=5:release=80,volume=4dB",
    "radio_light": "highpass=f=120,lowpass=f=6500,acompressor=threshold=-18dB:ratio=3:attack=10:release=120",
    "aside": "lowpass=f=2500",  # the level drop is done in the mix (mix.ASIDE_DB)
}


def chains(show) -> dict[str, str]:
    return DEFAULT_CHAINS | (show.fx or {})


def apply(wav: Path, names: list[str], show, cache: Cache) -> Path:
    """The wav with the named effects applied in order (cached)."""
    if not names:
        return wav
    table = chains(show)
    chain = ",".join(table[name] for name in names)
    digest = cache_mod.key({"source": wav.name, "chain": chain})
    target = cache.path("fx", digest, "wav")
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp.wav")
        run_ffmpeg(["-i", str(wav), "-af", chain, "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS),
                    "-c:a", "pcm_s16le", str(tmp)])
        tmp.replace(target)
    return target


_I_RE = re.compile(r"^\s*I:\s*(-?[\d.]+|-inf) LUFS", re.M)


def measure_lufs(path: Path) -> float:
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128", "-f", "null", "-"]
    out = subprocess.run(cmd, capture_output=True, text=True).stderr
    values = _I_RE.findall(out)
    if not values or values[-1] == "-inf":
        return -70.0
    return float(values[-1])


def loudness(path: Path, cache: Cache) -> float:
    """Integrated loudness of a cached file, remembered in build/cache/loudness.json."""
    table_path = cache.root / "loudness.json"
    table = json.loads(table_path.read_text(encoding="utf-8")) if table_path.exists() else {}
    if path.name not in table:
        table[path.name] = measure_lufs(path)
        write_bytes_atomic(table_path, json.dumps(table, indent=1).encode("utf-8"))
    return table[path.name]
