"""Conservative Railway volume cleanup. Dry-run by default.
Only removes aged disposable files from dedicated scratch directories.
Never traverses evidence, databases, originals, or audit result directories.
"""
from pathlib import Path
import argparse
import os
import time

ROOT = Path("/data")
SCRATCH = ("tmp", "temp", "cache", "scratch", "work_tmp")
PROTECTED = {".db", ".sqlite", ".sqlite3", ".odt", ".pdf", ".doc", ".docx", ".zip", ".rar", ".7z", ".xml", ".sig", ".p7s"}
def run(apply=False, hours=48):
    cutoff = time.time() - hours * 3600
    count = bytes_total = 0
    for name in SCRATCH:
        folder = ROOT / name
        if folder.is_symlink() or not folder.is_dir():
            continue
        for base, dirs, files in os.walk(folder, followlinks=False):
            dirs[:] = [d for d in dirs if not (Path(base) / d).is_symlink()]
            for filename in files:
                path = Path(base) / filename
                try:
                    if path.is_symlink() or path.suffix.lower() in PROTECTED:
                        continue
                    st = path.stat()
                    if not path.is_file() or st.st_mtime >= cutoff:
                        continue
                    count += 1
                    bytes_total += st.st_size
                    if apply:
                        path.unlink()
                except (OSError, PermissionError) as exc:
                    print(f"SKIP {path}: {exc}")
    print(f"mode={'DELETE' if apply else 'DRY_RUN'} files={count} bytes={bytes_total}")
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--hours", type=int, default=48)
    args = parser.parse_args()
    run(args.apply, args.hours)
