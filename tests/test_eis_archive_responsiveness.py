"""Real ASGI responsiveness while ZIP parsing and durable fsync are busy.

All payloads are synthetic. Rejection remains rejection; no lost/counted files.
"""
import asyncio
from contextlib import redirect_stdout
import io
import json
import threading
import unittest
from unittest.mock import patch

import httpx
from fastapi.responses import JSONResponse
import test_eis223_soap_archive as fixtures
from test_eis223_soap_archive import archive, fixture, REG, TOKEN
import v294_eis_bridge as bridge
import v381_audit_scheduling as scheduling


class ArchiveResponsiveness(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = fixtures.OfficialSoapArchiveTests()
        self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
        self.ns = self.fixture.ns
        @self.ns['app'].get('/fixture-health')
        async def healthy():
            return JSONResponse({'healthy': True})

    async def test_health_is_served_while_parser_is_held_in_a_worker(self):
        await self._held_operation('parse_notices')

    async def test_health_is_served_while_archive_fsync_is_held_in_a_worker(self):
        await self._held_operation('_atomic')

    async def _held_operation(self, operation):
        real = getattr(bridge, operation)
        entered, release = threading.Event(), threading.Event()
        loop_thread = threading.get_ident(); workers = []
        def held(*args):
            workers.append(threading.get_ident()); entered.set()
            if not release.wait(3):
                raise RuntimeError('Fixture was not released: operation blocked the event loop')
            return real(*args)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.ns['app']), base_url='http://fixture') as client:
            with patch.object(bridge, operation, side_effect=held):
                upload = asyncio.create_task(client.post('/api/v294/eis223/archive',
                    content=archive(fixture()), headers={'x-eis223-upload-token': TOKEN}))
                try:
                    self.assertTrue(await asyncio.wait_for(asyncio.to_thread(entered.wait, 1), 2))
                    health = await asyncio.wait_for(client.get('/fixture-health'), 1)
                    self.assertEqual(health.status_code, 200)
                    self.assertFalse(upload.done())
                    self.assertNotIn(loop_thread, workers)
                finally:
                    release.set()
                    result = await asyncio.wait_for(upload, 3)
                self.assertEqual(result.status_code, 200, result.text)
                self.assertEqual(result.json()['audited_documents_increment'], 0)
                self.assertEqual(result.json()['original_files_verified'], 0)

    async def test_cancelled_waiter_keeps_physical_capacity_until_thread_exits(self):
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        def held():
            entered.set()
            try:
                if not release.wait(3): raise RuntimeError('unreleased fixture')
                return True
            finally: finished.set()
        gate = scheduling._gate(); baseline = gate.active
        task = asyncio.create_task(bridge._archive_io(held))
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 1))
            self.assertEqual(gate.active, baseline+1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
            self.assertEqual(gate.active, baseline+1)
        finally:
            release.set()
            self.assertTrue(await asyncio.to_thread(finished.wait, 1))
            for _ in range(20):
                if gate.active == baseline: break
                await asyncio.sleep(.01)
        self.assertEqual(gate.active, baseline)

    async def test_registry_mismatch_stays_422_with_per_job_cooldown_and_safe_diagnostics(self):
        name, raw = fixture()
        bad = raw.replace(f'<registrationNumber>{REG}</registrationNumber>'.encode(),
                          b'<registrationNumber>32616233662</registrationNumber>')
        # Include a secret-looking arbitrary field; diagnostics must not copy it.
        bad = bad.replace(b'<version>', b'<opaque>NEVER_LOG_SYNTHETIC_SECRET</opaque><version>', 1)
        out = io.StringIO()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.ns['app']), base_url='http://fixture') as client:
            with redirect_stdout(out):
                reply = await client.post('/api/v294/eis223/archive', content=archive((name,bad)),
                                          headers={'x-eis223-upload-token':TOKEN})
        self.assertEqual(reply.status_code, 422)
        self.assertEqual(reply.headers['Retry-After'], '1800')
        self.assertEqual(reply.headers['X-EIS223-Rejection-Code'], 'xml_registry_mismatch')
        self.assertNotIn('NEVER_LOG_SYNTHETIC_SECRET', out.getvalue())
        self.assertNotIn(TOKEN, out.getvalue()); self.assertNotIn('https://', out.getvalue())
        diag = [line.split('V294 XML_IDENTITY_DIAGNOSTIC ',1)[1] for line in out.getvalue().splitlines()
                if line.startswith('V294 XML_IDENTITY_DIAGNOSTIC ')]
        self.assertEqual(len(diag), 1)
        member = json.loads(diag[0])['members'][0]
        self.assertEqual(member['filename_reg'], REG)
        self.assertEqual(member['own_reg'], '32616233662')
        self.assertEqual(member['xml_type'], 'purchaseNoticeKESMBO')
        self.assertFalse((self.fixture.root/'eis223_official_archive_inbox').exists())
        with self.fixture.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM purchases').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM findings').fetchone()[0], 0)

    async def test_invalid_identity_is_not_printed_as_text(self):
        name, raw = fixture()
        raw = raw.replace(f'<registrationNumber>{REG}</registrationNumber>'.encode(),
                          b'<registrationNumber>NEVER_LOG_SYNTHETIC_SECRET</registrationNumber>')
        result = bridge._registry_diagnostics(archive((name,raw)))
        self.assertEqual(result[0]['own_reg'], 'missing_or_invalid')
        self.assertNotIn('NEVER_LOG_SYNTHETIC_SECRET', json.dumps(result))


if __name__ == '__main__': unittest.main()
