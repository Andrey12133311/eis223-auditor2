"""Compare repeat extraction of a real local TAR.GZ fixture against a Git ref.

PYTHONPATH=scripts python scripts/benchmark_archive_expansion.py
Synthetic source documents, real compression, decompression and SHA256 reads;
no network, EIS originals, production throughput claims or simulated sleeps.
"""
import argparse
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
    old = types.ModuleType("archive_expansion_baseline")
    exec(compile(source, baseline + ":v310_archive_scan.py", "exec"), old.__dict__)
    random_bytes = random.Random(20261110)
    alphabet = "abcdefghijklmnopqrstuvwxyz0123456789 \n"
    with tempfile.TemporaryDirectory(prefix="eis-real-archive-benchmark-") as directory:
        root = Path(directory)
        archive = root / "documents.tar.gz"
        with tarfile.open(archive, "w:gz") as stream:
            for index in range(members):
                body = "".join(random_bytes.choices(alphabet, k=member_kib * 1024)).encode()
                info = tarfile.TarInfo(f"document-{index:03}.txt")
                info.size = len(body)
                stream.addfile(info, io.BytesIO(body))
            # Preserve the real incomplete-audit condition on the cache hit.
            body = b"fixture with no extractable PDF text"
            info = tarfile.TarInfo("unreadable.pdf")
            info.size = len(body)
            stream.addfile(info, io.BytesIO(body))

        def extract(module):
            return module.extract_members(archive, "32619000001", "A" * 32, root,
                                          lambda unused: "")

        expected = extract(old)
        started = time.perf_counter()
        first = extract(current)
        first_seconds = time.perf_counter() - started
        assert first == expected
        timings = {"before": [], "after": []}
        for _ in range(repeats):
            for label, module in (("before", old), ("after", current)):
                started = time.perf_counter()
                actual = extract(module)
                timings[label].append(time.perf_counter() - started)
                assert actual == expected
        before = statistics.median(timings["before"])
        after = statistics.median(timings["after"])
        return {"baseline": baseline, "scope": "real local synthetic TAR.GZ; repeat extraction only",
                "repeats": repeats, "original_bytes": archive.stat().st_size,
                "readable_members": len(expected[0]), "warnings": expected[1],
                "member_kib": member_kib, "cold_after_seconds": round(first_seconds, 6),
                "before_median_seconds": round(before, 6),
                "after_median_seconds": round(after, 6),
                "speedup": round(before / after, 2),
                "records_text_hashes_order_warnings_identical": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default="e640235")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--members", type=int, default=41)
    parser.add_argument("--member-kib", type=int, default=64)
    arguments = parser.parse_args()
    if not 1 <= arguments.members <= 90 or not 1 <= arguments.member_kib <= 512 or arguments.repeats < 1:
        parser.error("Use 1–90 members, 1–512 KiB per member and positive repeats")
    print(json.dumps(run(arguments.baseline, arguments.repeats, arguments.members,
                         arguments.member_kib), ensure_ascii=False, indent=2))
