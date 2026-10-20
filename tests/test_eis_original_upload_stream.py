"""Original intake commits only complete authenticated, allowlisted streams."""
import asyncio
from contextlib import contextmanager, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from starlette.requests import ClientDisconnect

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import v295_eis_originals as originals

REG = "32616233661"
UID = "A" * 32
TOKEN = "synthetic-original-upload-key-only"
RAW = b"%PDF-1.7\nsynthetic complete original"
URL = "https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid=" + UID


class OriginalUploadStreamTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        env = patch.dict(os.environ, {
            "EIS223_DATA_DIR": temp.name, "EIS223_UPLOAD_TOKEN": TOKEN,
            "EIS223_SOURCE_MODE": "OFFICIAL_EIS_VPS_ONLY"})
        env.start()
        self.addCleanup(env.stop)
        self.entry = {"uid": UID, "name": "fixture.pdf", "ext": ".pdf", "url": URL}
        self.manifest = self.root / "eis223_public_browser_intake" / REG / "manifest.json"
        self.manifest.parent.mkdir(parents=True)
        self.write_manifest(URL)
        self.state = self.root / "eis223_official_review_jobs" / (REG + ".json")
        self.ns = {"app": FastAPI()}
        originals.install(self.ns)

    def write_manifest(self, url):
        self.manifest.write_text(json.dumps({
            "source": "public_eis_223_browser", "reg_number": REG,
            "documents": [{**self.entry, "url": url}]}))

    def snapshot(self):
        return {str(path.relative_to(self.root)): path.read_bytes() if path.is_file() else None
                for path in self.root.rglob("*")}

    @contextmanager
    def untouched_intake(self):
        before = self.snapshot()
        with patch.object(originals, "assert_format", wraps=originals.assert_format) as validate, \
                patch.object(originals, "_store_original_version") as store, \
                patch.object(originals, "save_atomic") as write, \
                patch.object(originals, "_try_execution_lock") as queue:
            yield validate
            store.assert_not_called()
            write.assert_not_called()
            queue.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def asgi_post(self, events, *, headers=None, reg=REG, uid=UID, before_read=None):
        messages, reads = [], []
        pending = list(events)

        async def receive():
            if before_read is not None:
                before_read()
            reads.append(True)
            event = pending.pop(0)
            if isinstance(event, BaseException):
                raise event
            return event

        async def send(message):
            messages.append(message)

        path = f"/api/v295/eis223/original/{reg}/{uid}"
        headers = {"x-eis223-upload-token": TOKEN} if headers is None else headers
        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
                 "http_version": "1.1", "method": "POST", "scheme": "http", "path": path,
                 "raw_path": path.encode(), "query_string": b"", "root_path": "",
                 "headers": [(key.encode(), value.encode()) for key, value in headers.items()],
                 "server": ("testserver", 80), "client": ("fixture", 1234)}
        asyncio.run(self.ns["app"](scope, receive, send))
        start = next(message for message in messages if message["type"] == "http.response.start")
        content = b"".join(message.get("body", b"") for message in messages
                           if message["type"] == "http.response.body")
        return start, json.loads(content), len(reads)

    def test_disconnect_never_validates_stores_or_queues_partial_original(self):
        cases = ([{"type": "http.disconnect"}],
                 [{"type": "http.request", "body": RAW[:9], "more_body": True},
                  {"type": "http.request", "body": b"PRIVATE_PARTIAL_BODY", "more_body": True},
                  {"type": "http.disconnect"}],
                 [{"type": "http.request", "body": RAW, "more_body": True},
                  {"type": "http.disconnect"}])
        for retained in (False, True):
            if retained:
                originals._store_original_version(REG, UID, self.entry, b"%PDF-1.7\nprior original")
                self.state.parent.mkdir(parents=True)
                self.state.write_text(json.dumps({"reg_number": REG, "status": "queued",
                                                  "attempts": 2, "lease_token": "existing-lease"}))
            for events in cases:
                with self.subTest(retained=retained, events=len(events)), \
                        self.untouched_intake() as validate, redirect_stdout(io.StringIO()) as output:
                    start, body, reads = self.asgi_post(events)
                    self.assertEqual((start["status"], body, reads),
                                     (400, {"detail": "Incomplete original request body"}, len(events)))
                    self.assertNotIn(b"retry-after", dict(start["headers"]))
                    self.assertEqual(output.getvalue(), "")
                    validate.assert_not_called()

    def test_auth_identifiers_allowlist_and_declared_size_precede_body_read(self):
        token = {"x-eis223-upload-token": TOKEN}
        cases = (({"headers": {}}, 401),
                 ({"headers": {"x-eis223-upload-token": "wrong"}}, 401),
                 ({"reg": "bad-reg"}, 400), ({"uid": "bad-uid"}, 400),
                 ({"uid": "B" * 32}, 404),
                 ({"headers": {**token, "content-length": str(originals.MAX_BYTES + 1)}}, 413),
                 ({"headers": {"content-length": str(originals.MAX_BYTES + 1)}}, 401))
        for kwargs, expected in cases:
            with self.subTest(kwargs=kwargs), self.untouched_intake() as validate:
                start, _body, reads = self.asgi_post([{"type": "http.disconnect"}], **kwargs)
                self.assertEqual((start["status"], reads), (expected, 0))
                validate.assert_not_called()
        with patch.dict(os.environ, {"EIS223_UPLOAD_TOKEN": ""}), self.untouched_intake() as validate:
            start, _body, reads = self.asgi_post([{"type": "http.disconnect"}])
            self.assertEqual((start["status"], reads), (503, 0))
            validate.assert_not_called()
        self.write_manifest(URL.replace("zakupki.gov.ru", "zakupki.gov.ru.attacker.invalid"))
        with self.untouched_intake() as validate:
            start, _body, reads = self.asgi_post([{"type": "http.disconnect"}])
            self.assertEqual((start["status"], reads), (404, 0))
            validate.assert_not_called()

    def test_stream_size_and_complete_invalid_original_keep_413_and_422(self):
        with patch.object(originals, "MAX_BYTES", 8), self.untouched_intake() as validate:
            events = [{"type": "http.request", "body": b"x" * 8, "more_body": True},
                      {"type": "http.request", "body": b"x", "more_body": True},
                      {"type": "http.disconnect"}]
            start, body, reads = self.asgi_post(events)
            self.assertEqual((start["status"], body, reads),
                             (413, {"detail": "Original exceeds 25 MiB"}, 2))
            validate.assert_not_called()
        with self.untouched_intake() as validate, redirect_stdout(io.StringIO()) as output:
            start, body, reads = self.asgi_post([
                {"type": "http.request", "body": b"PRIVATE_INVALID_BODY", "more_body": False}])
            self.assertEqual((start["status"], body, reads),
                             (422, {"detail": "Not a PDF original"}, 1))
            validate.assert_called_once_with(b"PRIVATE_INVALID_BODY", ".pdf")
            self.assertNotIn("PRIVATE_INVALID_BODY", output.getvalue())

    def test_complete_chunked_upload_retains_hash_and_idempotent_completed_replay(self):
        events = [{"type": "http.request", "body": RAW[:9], "more_body": True},
                  {"type": "http.request", "body": RAW[9:], "more_body": True},
                  {"type": "http.request", "body": b"", "more_body": False}]
        # Keep the real admission/persistence path, with no background review
        # capacity in this upload fixture. Existing scheduler tests run workers.
        with patch.object(originals, "MAX_REVIEW_TASKS", 0):
            before = self.snapshot()
            start, body, reads = self.asgi_post(events, before_read=lambda: self.assertEqual(
                self.snapshot(), before))
            self.assertEqual((start["status"], reads, body["review_status"]), (200, 3, "queued"))
            self.assertEqual((body["accepted"], body["bytes"], body["audited_documents_increment"]),
                             (True, len(RAW), 0))
            path, receipt = originals.saved_document(REG, UID)
            digest = hashlib.sha256(RAW).hexdigest()
            self.assertEqual((path.read_bytes(), path.name, receipt["sha256"]),
                             (RAW, digest + ".pdf", digest))
            self.assertEqual(len(list((path.parent / "versions").glob("*.json"))), 1)
            completed = {**json.loads(self.state.read_text()), "status": "completed",
                         "source_version": originals._review_source_version(REG)}
            self.state.write_text(json.dumps(completed))
            before = self.snapshot()
            for _ in range(2):
                start, body, reads = self.asgi_post(events)
                self.assertEqual((start["status"], reads, body["review_status"]), (200, 3, "completed"))
                self.assertEqual(self.snapshot(), before)

    def test_stream_cancellation_and_unexpected_errors_propagate(self):
        for error in (asyncio.CancelledError(), RuntimeError("PRIVATE_STREAM_ERROR"),
                      sqlite3.OperationalError("database is locked")):
            with self.subTest(error=type(error).__name__), self.untouched_intake() as validate:
                with self.assertRaises(type(error)) as caught:
                    self.asgi_post([{"type": "http.request", "body": RAW[:9], "more_body": True}, error])
                self.assertIs(caught.exception, error)
                validate.assert_not_called()

    def test_disconnect_and_other_errors_outside_stream_are_not_hidden(self):
        events = [{"type": "http.request", "body": RAW, "more_body": False}]
        for function in ("auth", "allowed_documents", "assert_format", "_store_original_version"):
            for error in (ClientDisconnect(), asyncio.CancelledError(), RuntimeError("PRIVATE_INTAKE_ERROR")):
                with self.subTest(function=function, error=type(error).__name__):
                    before = self.snapshot()
                    with patch.object(originals, function, side_effect=error), \
                            patch.object(originals, "_try_execution_lock") as queue:
                        with self.assertRaises(type(error)) as caught:
                            self.asgi_post(events)
                        # An executor future preserves cancellation semantics,
                        # but need not preserve the CancelledError instance.
                        if not isinstance(error, asyncio.CancelledError):
                            self.assertIs(caught.exception, error)
                        queue.assert_not_called()
                    self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
