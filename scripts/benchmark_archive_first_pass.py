"""Compare first-pass TAR.GZ extraction with an earlier Git revision.

Uses a real synthetic compressed archive and fresh destinations on every pass.
The reported speed is local archive processing, not end-to-end EIS throughput.
Run: PYTHONPATH=scripts python scripts/benchmark_archive_first_pass.py
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import random
import statistics
import subprocess
import tarfile
import tempfile
import time
import types

import v310_archive_scan as current


def run(baseline, repeats, members, member_kib):
    source = subprocess.check_output([
        "git", "show", baseline + ":scripts/v310_archive_scan.py"], text=True)
    old = types.ModuleType("archive_first_pass_baseline")
    exec(compile(source, baseline + ":v310_archive_scan.py", "exec"), old.__dict__)
    random_bytes = random.Random(20261110)
    alphabet = "abcdefghijklmnopqrstuvwxyz0123456789 \n"
    with tempfile.TemporaryDirectory(prefix="eis-cold-archive-benchmark-") as directory:
        root = Path(directory)
        archive = root / "documents.tar.gz"
        with tarfile.open(archive, "w:gz") as stream:
            for index in range(members):
                body = "".join(random_bytes.choices(alphabet, k=member_kib * 1024)).encode()
                info = tarfile.TarInfo(f"document-{index:03}.txt")
                info.size = len(body)
                stream.addfile(info, io.BytesIO(body))
            body = b"fixture with no extractable PDF text"
            info = tarfile.TarInfo("unreadable.pdf")
            info.size = len(body)
            stream.addfile(info, io.BytesIO(body))

        def canonical(result):
            records, warnings = result
            for record in records:
                assert hashlib.sha256(record["path"].read_bytes()).hexdigest() == record["sha256"]
            return ([{key: value for key, value in record.items() if key != "path"}
                     for record in records], warnings)

        timings = {"before": [], "after": []}
        expected = None
        for iteration in range(repeats):
            # A new UID and output root on every pass prohibit expansion hits.
            # Alternate order so neither implementation is always measured first.
            versions = (("before", old), ("after", current))
            if iteration % 2:
                versions = tuple(reversed(versions))
            for label, module in versions:
                started = time.perf_counter()
                result = module.extract_members(archive, "32619000001", f"{iteration:032X}",
                                                root / label, lambda unused: "")
                timings[label].append(time.perf_counter() - started)
                actual = canonical(result)
                if expected is None:
                    expected = actual
                assert actual == expected
        before = statistics.median(timings["before"])
        after = statistics.median(timings["after"])
        return {"baseline": baseline,
                "scope": "real local synthetic TAR.GZ; fresh expansion destinations on every pass",
                "repeats": repeats, "original_bytes": archive.stat().st_size,
                "readable_members": len(expected[0]), "warnings": expected[1],
                "member_kib": member_kib,
                "before_median_seconds": round(before, 6),
                "after_median_seconds": round(after, 6),
                "speedup": round(before / after, 2),
                "records_text_hashes_order_warnings_identical": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default="20dcf2a")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--members", type=int, default=41)
    parser.add_argument("--member-kib", type=int, default=64)
    arguments = parser.parse_args()
    if not 1 <= arguments.members <= 90 or not 1 <= arguments.member_kib <= 512 or arguments.repeats < 1:
        parser.error("Use 1–90 members, 1–512 KiB per member and positive repeats")
    print(json.dumps(run(arguments.baseline, arguments.repeats, arguments.members,
                         arguments.member_kib), ensure_ascii=False, indent=2))
