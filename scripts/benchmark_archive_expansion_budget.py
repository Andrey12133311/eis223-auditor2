"""Measure bounded work on synthetic archives that exceed extraction limits.

Uses fresh destinations and the production limits. This measures local archive
processing, not complete audits or EIS download speed. The oversized TAR is
rejected as incomplete; the ZIP preserves the same fitting document prefix.
"""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import statistics
import subprocess
import tarfile
import tempfile
import time
import types
from unittest.mock import patch
import zipfile

import v310_archive_scan as current


def run(baseline, repeats):
    source = subprocess.check_output([
        "git", "show", baseline + ":scripts/v310_archive_scan.py"], text=True)
    previous = types.ModuleType("archive_expansion_budget_baseline")
    exec(compile(source, baseline + ":v310_archive_scan.py", "exec"), previous.__dict__)
    results = {"baseline": baseline,
               "scope": "local synthetic archives exceeding production byte limits; fresh destinations",
               "repeats": repeats, "scenarios": {}}
    with tempfile.TemporaryDirectory(prefix="eis-archive-budget-benchmark-") as directory:
        root = Path(directory)
        oversized = root / "oversized.tar.gz"
        with tarfile.open(oversized, "w:gz") as stream:
            body = b"x" * (current.MAX_EXPANDED + 1024 * 1024)
            info = tarfile.TarInfo("oversized.txt")
            info.size = len(body)
            stream.addfile(info, io.BytesIO(body))
        expanded = root / "expanded.zip"
        with zipfile.ZipFile(expanded, "w", compression=zipfile.ZIP_DEFLATED) as stream:
            for index in range(100):
                stream.writestr(f"document-{index:03}.txt", b"x" * (1024 * 1024))

        for scenario, archive, reader_class in (("oversized_tar", oversized, gzip._GzipReader),
                                                 ("expanded_zip", expanded, zipfile.ZipExtFile)):
            timings = {"before": [], "after": []}
            measurements = {}
            zip_evidence = None
            for repeat in range(repeats):
                versions = (("before", previous), ("after", current))
                if repeat % 2:
                    versions = tuple(reversed(versions))
                for label, module in versions:
                    decompressed = 0
                    original = reader_class.read

                    def counted(reader, *args, **kwargs):
                        nonlocal decompressed
                        data = original(reader, *args, **kwargs)
                        decompressed += len(data)
                        return data

                    with patch.object(reader_class, "read", counted):
                        started = time.perf_counter()
                        records, warnings = module.extract_members(archive, "32619000001",
                            f"{repeat:032X}", root / scenario / label, lambda path: path.read_text())
                        timings[label].append(time.perf_counter() - started)
                    assert warnings, "An archive exceeding the budget must remain incomplete"
                    if scenario == "expanded_zip":
                        evidence = [(record["name"], record["sha256"],
                                     hashlib.sha256(record["text"].encode()).hexdigest())
                                    for record in records]
                        if zip_evidence is None:
                            zip_evidence = evidence
                        assert evidence == zip_evidence, "Fitting ZIP evidence changed"
                    measurements[label] = {"decompressed_bytes": decompressed,
                                           "readable_documents": len(records),
                                           "incomplete_warning": True}
            for label in timings:
                measurements[label]["median_seconds"] = round(statistics.median(timings[label]), 6)
            measurements["compressed_bytes"] = archive.stat().st_size
            measurements["speedup"] = round(statistics.median(timings["before"]) /
                                               statistics.median(timings["after"]), 2)
            results["scenarios"][scenario] = measurements
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default="c687b88")
    parser.add_argument("--repeats", type=int, default=3)
    arguments = parser.parse_args()
    if not 1 <= arguments.repeats <= 20:
        parser.error("Use 1–20 repeats")
    print(json.dumps(run(arguments.baseline, arguments.repeats), ensure_ascii=False, indent=2))
