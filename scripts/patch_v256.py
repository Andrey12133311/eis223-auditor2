#!/usr/bin/env python3
"""Patch a verified private Railway recovery entry.py onto ephemeral disk.

Fail closed on source changes. Do NOT modify the original snapshot, DB or files.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import tempfile
from pathlib import Path

EXPECTED_ORIGINAL_SHA256 = "67a27c73a50710fa5f5fab8f8fb052e6ed2d5d53e59e3f03d030dc2f17ebf463"

RULES = (
    (
        "            except (asyncio.TimeoutError,ns['httpx'].TimeoutException,ns['httpx'].ConnectError) as exc:\n"
        "                health['failures']+=1",
        "            except (asyncio.TimeoutError,ns['httpx'].TransportError) as exc:\n"
        "                health['failures']+=1",
    ),
    (
        "                except (asyncio.TimeoutError,ns['httpx'].TimeoutException,ns['httpx'].ConnectError) as exc:\n"
        "                    health['failures']+=1",
        "                except (asyncio.TimeoutError,ns['httpx'].TransportError) as exc:\n"
        "                    health['failures']+=1",
    ),
    (
        "        d=json.loads(job['payload']);d.setdefault('meta',{});d.setdefault('text','')",
        "        d=json.loads(job['payload']);d['meta']=d.get('meta') if isinstance(d.get('meta'),dict) else {};d.setdefault('text','')",
    ),
    (
        "            for u in [d.get('url'),d['meta'].get('mirror_url'),d.get('final_url')]+list(d['meta'].get('alt_urls') or []):\n"
        "                if u and u not in candidates:candidates.append(u)",
        "            alt_urls=d['meta'].get('alt_urls') or []\n"
        "            if isinstance(alt_urls,str):alt_urls=[alt_urls]\n"
        "            if not isinstance(alt_urls,(tuple,list)):alt_urls=[]\n"
        "            for u in [d.get('url'),d['meta'].get('mirror_url'),d.get('final_url')]+list(alt_urls):\n"
        "                if isinstance(u,str) and u.startswith(('https://','http://')) and u not in candidates:candidates.append(u)",
    ),
)


def patch_text(source: str) -> str:
    """Each anchor must exist exactly once; never apply an approximate patch."""
    for old, new in RULES:
        count = source.count(old)
        if count != 1:
            raise RuntimeError(f"Expected one patch anchor, found {count}: {old[:100]!r}")
        source = source.replace(old, new, 1)
    compile(source, "entry.py", "exec")
    return source


def patch_file(source_path: Path, target_path: Path) -> str:
    source_path = Path(source_path).resolve(strict=True)
    target_path = Path(target_path)
    if source_path == target_path.resolve():
        raise ValueError("Never overwrite the private original snapshot")
    contents = source_path.read_bytes()
    digest = hashlib.sha256(contents).hexdigest()
    if digest != EXPECTED_ORIGINAL_SHA256:
        raise RuntimeError("Source checksum differs from inspected live code; refusing an unsafe patch")
    patched = patch_text(contents.decode("utf-8"))
    target_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    target_path.parent.chmod(0o700)
    fd, temporary = tempfile.mkstemp(prefix=".entry-v256-", dir=target_path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(patched)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, target_path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return hashlib.sha256(target_path.read_bytes()).hexdigest()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    print("V256_PATCH_OK", patch_file(args.source, args.target))
