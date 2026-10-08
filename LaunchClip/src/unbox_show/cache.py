"""Content-addressed cache under build/cache/. A build never deletes from it."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def key(data: dict) -> str:
    """SHA-256 of everything that influences a result."""
    blob = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def write_bytes_atomic(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    return path


class Cache:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.manifest_path = root / "manifest.json"
        self._manifest: dict | None = None

    def path(self, kind: str, digest: str, ext: str) -> Path:
        return self.root / kind / f"{digest}.{ext}"

    def get(self, kind: str, digest: str, ext: str) -> Path | None:
        path = self.path(kind, digest, ext)
        return path if path.exists() else None

    def put(self, kind: str, digest: str, ext: str, data: bytes) -> Path:
        return write_bytes_atomic(self.path(kind, digest, ext), data)

    def get_json(self, kind: str, digest: str) -> dict | None:
        path = self.get(kind, digest, "json")
        return json.loads(path.read_text(encoding="utf-8")) if path else None

    def put_json(self, kind: str, digest: str, data: dict) -> Path:
        return self.put(kind, digest, "json", json.dumps(data, ensure_ascii=False).encode("utf-8"))

    # Manifest: which real take is current for every entry id. Fake runs are not recorded.

    @property
    def manifest(self) -> dict:
        if self._manifest is None:
            if self.manifest_path.exists():
                self._manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            else:
                self._manifest = {}
        return self._manifest

    def record(self, entry_id: str, digest: str, duration: float, characters: int) -> None:
        previous = self.manifest.get(entry_id, {})
        if previous.get("hash") == digest:
            return
        self.manifest[entry_id] = {
            "hash": digest,
            "duration": round(duration, 3),
            "characters": characters,
            "date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    def save_manifest(self) -> None:
        if self._manifest is None:
            return
        text = json.dumps(dict(sorted(self._manifest.items())), indent=2, ensure_ascii=False)
        write_bytes_atomic(self.manifest_path, (text + "\n").encode("utf-8"))
