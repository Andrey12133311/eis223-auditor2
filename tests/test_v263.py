"""Fast CI: fail-closed patching, bounded concurrency and safe feed sampling."""
from __future__ import annotations
import asyncio
import inspect
import os
import tempfile
import time
import unittest
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from patch_v263 import (patch,patch_text,BLOCK_NEW,FEED_NEW,
                        BLOCK_HASH,FEED_HASH,START,END)

class Tests(unittest.TestCase):
    def test_rejects_unknown_private_source_and_keeps_it_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'entry.py'
            p.write_text('print(123)\n')
            with self.assertRaisesRegex(RuntimeError,'anchors'):
                patch(p)
            self.assertEqual(p.read_text(),'print(123)\n')
    def test_checksum_guard_and_immutable_counters(self):
        self.assertEqual(len(BLOCK_HASH),64)
        self.assertEqual(len(FEED_HASH),64)
        self.assertIn('asyncio.Semaphore(parallel)',BLOCK_NEW)
        self.assertIn('await asyncio.gather',BLOCK_NEW)
        self.assertIn('max(2,min(4',BLOCK_NEW)
        self.assertIn('max(4,min(20',BLOCK_NEW)
        self.assertIn("max(0,min(3",FEED_NEW)
        self.assertIn('seen.add(reg)',FEED_NEW)
        for text in [BLOCK_NEW,FEED_NEW]:
            self.assertNotIn('DELETE FROM',text)
            self.assertNotIn('status=\\'checked\\'',text)
            self.assertNotIn('stats.purchases',text)
            self.assertNotIn('UPDATE purchases',text)
    def test_parallelism_limited_and_faster_than_serial_simulation(self):
        async def trial():
            gate=asyncio.Semaphore(4)
            peak=0
            inflight=0
            saved=[]
            async def one(i):
                nonlocal peak,inflight
                async with gate:
                    inflight+=1
                    peak=max(peak,inflight)
                    await asyncio.sleep(.035)
                    saved.append(i)
                    inflight-=1
            start=time.monotonic()
            await asyncio.gather(*(one(i) for i in range(16)))
            return len(saved),peak,time.monotonic()-start
        n,peak,elapsed=asyncio.run(trial())
        self.assertEqual(n,16)
        self.assertEqual(peak,4)
        self.assertLess(elapsed,.37)
    def test_known_private_patch_fingerprints_not_changed(self):
        self.assertIn('for _ in range(min(6',START)
        self.assertIn('V217 PURCHASE_WAIT',END)
        self.assertIn("progress['backfill_error']",FEED_NEW)

if __name__=='__main__':
    unittest.main()
