#!/usr/bin/env python3
"""Run a verified backup of the full Railway-patched EIS 223 application.

Requires a snapshot created by railway_backup_runtime.py on the LIVE web instance.
The snapshot never leaves the private persistent /data volume.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(os.environ.get("EIS223_RECOVERY_ROOT", "/data/_eis223_runtime_recovery"))


def checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def restore() -> tuple[Path, int]:
    pointer = json.loads((ROOT / "current.json").read_text(encoding="utf-8"))
    name = pointer["snapshot"]
    if not isinstance(name, str) or not name.startswith("snapshot-") or "/" in name or ".." in name:
        raise RuntimeError("Invalid recovery snapshot identifier")
    root = ROOT.resolve(strict=True)
    folder = (root / name).resolve(strict=True)
    if folder.parent != root:
        raise RuntimeError("Snapshot escaped recovery directory")
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    hashes = manifest.get("file_sha256", {})
    for filename in ("entry.py", "all_documents.py", "program-env.json"):
        if filename not in hashes:
            raise RuntimeError("Missing required recovery artifact " + filename)
        if checksum(folder / filename) != hashes[filename]:
            raise RuntimeError("Corrupt recovery artifact " + filename)
    variables = json.loads((folder / "program-env.json").read_text(encoding="utf-8"))
    if not isinstance(variables, dict):
        raise RuntimeError("Recovery environment has invalid format")
    count = 0
    for name, value in variables.items():
        if isinstance(name, str) and isinstance(value, str) and not name.startswith("RAILWAY_"):
            if name not in os.environ:
                os.environ[name] = value
                count += 1
    sys.path.insert(0, str(folder))
    os.environ.setdefault("EIS223_DATA_DIR", "/data")
    return folder, count


def main() -> None:
    folder, count = restore()
    print("RECOVERY_VERIFIED", folder.name, "RESTORED_ENV_KEYS", count, flush=True)
    if "--verify-only" in sys.argv:
        return
    import uvicorn
    uvicorn.run("entry:app", host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))


if __name__ == "__main__":
    main()
