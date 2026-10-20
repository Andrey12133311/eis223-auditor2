"""Read-only duplicate admission, complete INN pagination, off-loop I/O."""
import asyncio
import concurrent.futures
from contextlib import closing
import sqlite3
import threading
import unittest
from unittest.mock import patch

from starlette.requests import Request
from tests import test_eis_server_worker as fixtures
from tests import test_personal_inn_search as personal

worker, queue = fixtures.worker, fixtures.queue
REG, INN = fixtures.REG, personal.INN


class QueueReadAdmissionTests(unittest.TestCase):
    setUp = fixtures.ServerWorkerTests.setUp
    review = fixtures.ServerWorkerTests.review
    row = fixtures.ServerWorkerTests.row

    def enqueue(self, kind='inn', target=INN, page=1, parent='', manual=False):
        return queue._enqueue(self.ns, kind, target, page, 0, parent, manual)

    def test_duplicate_is_read_only_while_another_writer_is_reserved(self):
        for kind, target in (('inn', INN), ('reg', REG)):
            with self.subTest(kind=kind):
                first, _ = self.enqueue(kind, target)
                queries = []
                def connect():
                    db = sqlite3.connect(self.home/'queue.sqlite', timeout=.02)
                    db.set_trace_callback(queries.append)
                    return db
                blocker = connect()
                try:
                    blocker.execute('BEGIN IMMEDIATE')
                    queries.clear()
                    with patch.dict(self.ns, {'conn': connect}):
                        duplicate, created = self.enqueue(kind, target)
                    self.assertFalse(created)
                    self.assertEqual(duplicate, first)
                    self.assertFalse(any(sql.upper().startswith(('BEGIN', 'INSERT', 'UPDATE', 'DELETE'))
                                         for sql in queries), queries)
                finally:
                    blocker.rollback()
                    blocker.close()

    def test_first_request_still_needs_a_writer_and_cannot_acknowledge_failure(self):
        connector = lambda: sqlite3.connect(self.home/'queue.sqlite', timeout=.02)
        blocker = connector()
        try:
            blocker.execute('BEGIN IMMEDIATE')
            with patch.dict(self.ns, {'conn': connector}):
                with self.assertRaises(sqlite3.OperationalError):
                    self.enqueue()
        finally:
            blocker.rollback()
            blocker.close()
        with closing(connector()) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM v301_search_jobs').fetchone()[0], 0)

    def test_completed_first_page_reuses_root_while_later_page_is_active(self):
        root, _ = self.enqueue()
        child, _ = self.enqueue(page=2, parent=root['id'])
        for state in ('queued', 'leased', 'deferred'):
            with self.subTest(state=state):
                with self.ns['conn']() as db:
                    db.execute("UPDATE v301_search_jobs SET status='done' WHERE id=?", (root['id'],))
                    db.execute('UPDATE v301_search_jobs SET status=? WHERE id=?', (state, child['id']))
                duplicate, created = self.enqueue()
                self.assertFalse(created)
                self.assertEqual(duplicate['id'], root['id'])
                self.assertEqual(duplicate['status'], 'done')
                self.assertEqual(self.row(child['id'])['status'], state)

    def test_finished_search_can_start_again(self):
        root, _ = self.enqueue()
        child, _ = self.enqueue(page=2, parent=root['id'])
        with self.ns['conn']() as db:
            db.execute("UPDATE v301_search_jobs SET status='done'")
        fresh, created = self.enqueue()
        self.assertTrue(created)
        self.assertNotEqual(fresh['id'], root['id'])
        self.assertEqual(self.row(child['id'])['status'], 'done')

    def test_foreign_inn_child_does_not_extend_completed_search(self):
        root, _ = self.enqueue()
        self.enqueue(target=personal.OTHER_INN, page=2, parent=root['id'])
        with self.ns['conn']() as db:
            db.execute("UPDATE v301_search_jobs SET status='done' WHERE id=?", (root['id'],))
        _, created = self.enqueue()
        self.assertTrue(created)

    def test_concurrent_first_requests_create_one_job(self):
        barrier = threading.Barrier(2)
        def submit():
            barrier.wait(timeout=5)
            return self.enqueue()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(submit) for _ in range(2)]
            rows = [future.result(timeout=10) for future in futures]
        self.assertEqual(len({row[0]['id'] for row in rows}), 1)
        self.assertEqual(sum(row[1] for row in rows), 1)

    def test_manual_reg_input_is_recorded_even_when_download_already_queued(self):
        first, _ = self.enqueue('reg', REG)
        repeated, created = self.enqueue('reg', REG, manual=True)
        self.assertFalse(created)
        self.assertEqual(repeated['id'], first['id'])
        with closing(self.ns['conn']()) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM v319_manual_inputs').fetchone()[0], 1)


class PersonalInnPerformanceTests(unittest.TestCase):
    setUp = personal.PersonalInnSearchTests.setUp
    account_connect = personal.PersonalInnSearchTests.account_connect
    gate_session = staticmethod(personal.PersonalInnSearchTests.gate_session)
    headers = personal.PersonalInnSearchTests.headers
    start = personal.PersonalInnSearchTests.start
    snapshot = personal.PersonalInnSearchTests.snapshot
    purchase = personal.PersonalInnSearchTests.purchase
    count = personal.PersonalInnSearchTests.count

    def test_real_match_is_not_hidden_by_250_newer_legacy_false_associations(self):
        self.purchase(REG, INN)
        with self.ns['conn']() as db:
            for index in range(250):
                reg = '3262'+str(index).zfill(7)
                db.execute('INSERT INTO purchases VALUES(?,?,?,?,?,?,?,?,?,?)',
                           (reg, 'Other buyer', 'Other', personal.OTHER_INN, '100', 100, 0, 0, 0, '2026-10-20'))
                db.execute('INSERT INTO v301_inn_purchases VALUES(?,?,?)', (INN, reg, '2026-10-20'))
        with patch.dict(personal.os.environ, {'EIS223_SOURCE_MODE': 'legacy'}):
            response = self.start()
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual([row['reg_number'] for row in response.json()['existing']], [REG])

    def test_verified_results_use_one_read_only_purchase_query(self):
        self.purchase(REG, INN)
        queries = []
        def connect():
            db = sqlite3.connect(self.queue_path)
            db.set_trace_callback(queries.append)
            return db
        rows = personal.manual._verified_inn_results({'conn': connect}, INN)
        self.assertEqual([row['reg_number'] for row in rows], [REG])
        self.assertEqual(len(queries), 1, queries)
        self.assertTrue(queries[0].lstrip().upper().startswith('SELECT'))

    def test_resubmission_during_pagination_preserves_owner_scoping(self):
        root = self.start().json()['id']
        queue._enqueue(self.ns, 'inn', INN, 2, 12, root, False)
        with self.ns['conn']() as db:
            db.execute("UPDATE v301_search_jobs SET status='done' WHERE id=?", (root,))
        self.assertEqual(self.snapshot(root, 'bob').status_code, 404)
        same = self.start().json()
        self.assertEqual(same['id'], root)
        self.assertFalse(same['new_request'])
        self.assertEqual(self.snapshot(root).json()['search_pages_pending'], 1)
        other = self.start(owner='bob').json()
        self.assertEqual(other['id'], root)
        self.assertEqual(self.snapshot(root, 'bob').status_code, 200)
        self.assertEqual(self.count('v301_search_jobs'), 2)


class WorkerEventLoopTests(unittest.TestCase):
    setUp = fixtures.ServerWorkerTests.setUp
    review = fixtures.ServerWorkerTests.review

    def request(self, path):
        return Request({'type': 'http', 'method': 'GET', 'path': path,
                        'headers': [(b'x-eis223-upload-token', fixtures.KEY.encode())]})

    def endpoint(self, path):
        return next(route.endpoint for route in self.ns['app'].routes if route.path == path)

    def test_synchronous_review_fallback_is_not_run_on_event_loop(self):
        async def check():
            event_thread = threading.get_ident()
            def review(reg):
                self.assertNotEqual(threading.get_ident(), event_thread)
                return {'status': 'queued'}
            async def data(_):
                return {}
            result = {'reg_number': REG, 'originals_received': 1, 'replayed': False}
            with patch.dict(self.ns, {'_v295_enqueue_review': review}), \
                 patch.object(worker, 'body', data), \
                 patch.object(worker, 'report', return_value=result):
                actual = await self.endpoint('/api/eis-worker/jobs/{identifier}/report')(
                    'a'*24, self.request('/api/eis-worker/jobs/'+'a'*24+'/report'))
            self.assertEqual(actual['review_status'], 'queued')
        asyncio.run(check())

    def test_status_database_and_health_are_not_run_on_event_loop(self):
        async def check():
            event_thread = threading.get_ident()
            original_connect = self.ns['conn']
            def connect():
                self.assertNotEqual(threading.get_ident(), event_thread)
                return original_connect()
            def health(_):
                self.assertNotEqual(threading.get_ident(), event_thread)
                return {'active': True}
            with patch.dict(self.ns, {'conn': connect}), patch.object(worker, 'health', health):
                actual = await self.endpoint('/api/eis-worker/status')(self.request('/api/eis-worker/status'))
            self.assertTrue(actual['server']['active'])
        asyncio.run(check())


if __name__ == '__main__':
    unittest.main()
