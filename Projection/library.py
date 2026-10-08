"""index.jsonl bookkeeping and "which video is the previous one" selection."""
from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import config as cfg

logger = logging.getLogger(__name__)


@dataclass
class Entry:
    id: str
    file: str
    parent_id: Optional[str]
    duration: float
    hidden: bool
    seed: bool
    path: Path  # resolved absolute-ish path to the mp4

    @property
    def wav_path(self) -> Path:
        return self.path.with_suffix(".wav")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    logger.warning("Ongeldige regel %d in %s: %s", line_no, path, exc)
    except OSError as exc:
        logger.error("Kon %s niet lezen: %s", path, exc)
    return rows


def load_recordings(config: dict[str, Any]) -> list[Entry]:
    idx_path = cfg.index_path(config)
    videos = cfg.videos_dir(config)
    entries = []
    for row in _read_jsonl(idx_path):
        try:
            entries.append(
                Entry(
                    id=row["id"],
                    file=row["file"],
                    parent_id=row.get("parent_id"),
                    duration=float(row.get("duration", 0.0)),
                    hidden=bool(row.get("hidden", False)),
                    seed=bool(row.get("seed", False)),
                    path=videos / row["file"],
                )
            )
        except KeyError as exc:
            logger.warning("Regel in %s mist veld %s, wordt overgeslagen", idx_path, exc)
    return entries


def append_entry(config: dict[str, Any], entry: dict[str, Any]) -> None:
    idx_path = cfg.index_path(config)
    idx_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(idx_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.error("Kon opname niet toevoegen aan %s: %s", idx_path, exc)


def set_hidden(config: dict[str, Any], entry_id: str, hidden: bool = True) -> bool:
    """Rewrite index.jsonl with the given entry's hidden flag updated."""
    idx_path = cfg.index_path(config)
    rows = _read_jsonl(idx_path)
    found = False
    for row in rows:
        if row.get("id") == entry_id:
            row["hidden"] = hidden
            found = True
    if not found:
        logger.warning("Kon opname %s niet vinden in %s om te verbergen", entry_id, idx_path)
        return False
    try:
        with open(idx_path, "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.error("Kon %s niet herschrijven: %s", idx_path, exc)
        return False
    return True


def load_seeds(config: dict[str, Any]) -> list[Entry]:
    """Seeds are just .mp4 files dropped in media/seeds/, optionally with a
    matching .wav sidecar. No manual index entry is required for them."""
    seeds_path = cfg.seeds_dir(config)
    if not seeds_path.exists():
        return []
    entries = []
    for mp4 in sorted(seeds_path.glob("*.mp4")):
        entries.append(
            Entry(
                id=mp4.stem,
                file=mp4.name,
                parent_id=None,
                duration=0.0,
                hidden=False,
                seed=True,
                path=mp4,
            )
        )
    return entries


def select_previous(config: dict[str, Any]) -> Optional[Entry]:
    """Pick the video to show as "the previous story" in IDLE, according to
    config['selection_strategy']. Returns None if nothing is available at
    all (empty library and no seeds)."""
    strategy = config.get("selection_strategy", "latest")

    if strategy == "random_seed":
        seeds = load_seeds(config)
        if not seeds:
            logger.warning("selection_strategy=random_seed maar geen seeds gevonden")
            return None
        return random.choice(seeds)

    recordings = [e for e in load_recordings(config) if not e.hidden]

    if not recordings:
        seeds = load_seeds(config)
        if not seeds:
            return None
        return random.choice(seeds)

    if strategy == "latest":
        return recordings[-1]
    if strategy == "random_recent":
        n = max(1, int(config.get("selection_n", 5)))
        pool = recordings[-n:]
        return random.choice(pool)
    if strategy == "random_any":
        return random.choice(recordings)

    logger.warning("Onbekende selection_strategy '%s', val terug op 'latest'", strategy)
    return recordings[-1]


def latest_recording(config: dict[str, Any]) -> Optional[Entry]:
    recordings = load_recordings(config)
    if not recordings:
        return None
    return recordings[-1]
