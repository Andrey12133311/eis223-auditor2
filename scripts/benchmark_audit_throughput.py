"""Reproducible local scheduling benchmark, with no EIS/production requests.

Run: PYTHONPATH=scripts python scripts/benchmark_audit_throughput.py
Uses real before/after stored_docs and faster_tick implementations; synthetic
extractor/review delays isolate scheduling and do not predict live EIS speed.
"""
import argparse
import asyncio
from contextlib import ExitStack
import json
from pathlib import Path
import statistics
import subprocess
import tempfile
import time
import types
from unittest.mock import patch

import v332_all_procurements_audit as current_general
import v342_thematic_queue as theme
import v345_thematic_accelerator as current_thematic
from v381_audit_scheduling import background_audit


def baseline_module(ref, filename, name):
    code = subprocess.check_output(["git", "show", f"{ref}:scripts/{filename}"], text=True)
    module = types.ModuleType(name)
    exec(compile(code, f"{ref}:{filename}", "exec"), module.__dict__)
    return module


async def file_benchmark(module, home):
    rows = []
    for i in range(36):
        file = home / f"{i}.pdf"
        file.write_bytes(f"Synthetic original bytes {i}".encode())
        rows.append({"id":i, "local_path":str(file), "name":file.name})

    def extract(file, extractor):
        time.sleep(.18 if int(file.stem) % 12 == 0 else .012)
        return f"Synthetic text for {file.name}"

    with patch.object(module, "root", return_value=home), \
         patch.object(module, "source_verified", return_value=False), \
         patch.object(module, "document_text", side_effect=extract):
        started = time.perf_counter()
        with background_audit():
            docs, unreadable = await module.stored_docs({"extract_text":lambda p:"unused"}, "32610000000", rows)
        elapsed = time.perf_counter() - started
    assert unreadable == 0
    assert [doc["name"] for doc in docs] == [row["name"] for row in rows]
    assert all(doc["meta"]["source"] == "existing_local_unverified" for doc in docs)
    return elapsed


async def thematic_benchmark(module):
    items = [{"batch":"fixture", "reg":str(i), "ready":1,
              "doc_id":1, "categories":["dates"]} for i in range(36)]
    persisted = []

    async def review(ns, reg, categories):
        await asyncio.sleep(.18 if reg == "0" else .012)
        return "completed", []

    def persist(ns, results):
        persisted.append((time.perf_counter(), len(results)))

    with patch.object(theme, "storage_health", return_value={"blocked":False}), \
         patch.object(theme, "independent_review", side_effect=review), \
         patch.object(module, "_prepare", return_value=items), \
         patch.object(module, "_persist", side_effect=persist), \
         patch.object(module, "_request_missing_originals", return_value=0):
        started = time.perf_counter()
        await module.faster_tick({})
        elapsed = time.perf_counter() - started
    assert sum(count for stamp, count in persisted) == 36
    return {"first_durable_result_ms":(persisted[0][0]-started)*1000,
            "batch_ms":elapsed*1000}


async def run(ref, repeats):
    old_general = baseline_module(ref, "v332_all_procurements_audit.py", "audit_before")
    old_thematic = baseline_module(ref, "v345_thematic_accelerator.py", "theme_before")
    result = {"baseline":ref, "repeats":repeats,
              "scope":"synthetic local scheduling fixtures; no live EIS or legal performance claim"}
    with tempfile.TemporaryDirectory() as directory:
        home = Path(directory)
        for name, general, thematic in (("before", old_general, old_thematic),
                                        ("after", current_general, current_thematic)):
            files = [await file_benchmark(general, home) for _ in range(repeats)]
            themes = [await thematic_benchmark(thematic) for _ in range(repeats)]
            result[name] = {"36_file_median_ms":statistics.median(files)*1000,
                            "thematic_first_result_median_ms":statistics.median(t["first_durable_result_ms"] for t in themes),
                            "thematic_36_item_median_ms":statistics.median(t["batch_ms"] for t in themes)}
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default="19c77c2")
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    asyncio.run(run(args.baseline, args.repeats))
