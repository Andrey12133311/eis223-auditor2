#!/usr/bin/env python3
"""Snapshot the running EIS 223 auditor before Railway patch-variable cleanup.

Run INSIDE the currently healthy Railway web container, not during a failed build.
Nothing is deleted or modified outside /data/_eis223_runtime_recovery.
Never copy the private snapshot into a public Git repository.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(os.environ.get("EIS223_RECOVERY_ROOT", "/data/_eis223_runtime_recovery"))
SOURCES = {"entry.py": Path("/tmp/entry.py"), "all_documents.py": Path("/tmp/all_documents.py")}
SECRET = re.compile(
    r"(TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|PRIVATE_KEY|ACCESS_KEY|"
    r"CREDENTIAL|DATABASE_URL|DB_URL|AUTHORIZATION|COOKIE|SESSION)",
    re.I,
)


def checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_private(path: Path, contents: bytes) -> None:
    path.write_bytes(contents)
    path.chmod(0o600)


def snapshot() -> None:
    if not SOURCES["entry.py"].is_file():
        raise RuntimeError("No /tmp/entry.py found; select the currently RUNNING web deployment")
    ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    ROOT.chmod(0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = "snapshot-" + stamp + "-" + uuid4().hex[:8]
    folder = ROOT / name
    folder.mkdir(mode=0o700)
    hashes: dict[str, str] = {}
    for filename, origin in SOURCES.items():
        if origin.is_file():
            target = folder / filename
            shutil.copyfile(origin, target)
            target.chmod(0o600)
            hashes[filename] = checksum(target)
    # Secrets remain in Railway's small, separately configured runtime variables.
    # Copy non-secret program blobs to the private persistent volume only.
    program_vars = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("RAILWAY_") and not SECRET.search(k)
    }
    backup_path = folder / "program-env.json"
    write_private(backup_path, json.dumps(program_vars, ensure_ascii=False).encode("utf-8"))
    hashes[backup_path.name] = checksum(backup_path)
    manifest = {
        "snapshot": name,
        "saved_at_utc": datetime.now(timezone.utc).isoformat(),
        "file_sha256": hashes,
        "archived_variable_count": len(program_vars),
        "excluded_secret_variable_count": sum(bool(SECRET.search(k)) for k in os.environ),
    }
    write_private(folder / "manifest.json", json.dumps(manifest, indent=2).encode("utf-8"))
    # Only switch the pointer after every file has been written and verified.
    for filename, expected in hashes.items():
        if checksum(folder / filename) != expected:
            raise RuntimeError("Snapshot verification failed for " + filename)
    pointer = ROOT / "current.json"
    temp_pointer = ROOT / ("current." + uuid4().hex + ".tmp")
    write_private(temp_pointer, json.dumps({"snapshot": name}).encode("utf-8"))
    os.replace(temp_pointer, pointer)
    print("SNAPSHOT_OK", name)
    print("PROGRAM_VARIABLES_SAVED", len(program_vars))
    print("FILES_SAVED", ",".join(sorted(hashes)))
    print("PRIVATE_PATH", str(folder))
    print("Do not delete Railway patch variables until this snapshot is verified.")


if __name__ == "__main__":
    snapshot()
