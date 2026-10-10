#!/usr/bin/env python3
"""Opt-in startup from a verified private snapshot with an ephemeral patch."""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

from railway_start_restored import restore
from patch_v256 import patch_file


def main() -> None:
    folder, count = restore()
    ephemeral = Path(tempfile.gettempdir()) / "eis223_v256_private"
    target = ephemeral / "entry.py"
    digest = patch_file(folder / "entry.py", target)
    sys.path.insert(0, str(ephemeral))
    print("V256_SAFE_PATCH_READY", digest[:12], "RESTORED_ENV_KEYS", count, flush=True)
    if "--verify-only" in sys.argv:
        return
    import uvicorn
    uvicorn.run("entry:app", host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))


if __name__ == "__main__":
    main()
