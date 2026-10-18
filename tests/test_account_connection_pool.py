"""Identity pool lifecycle and transaction contract without a live database."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import os
import sqlite3
import threading
import unittest
from unittest.mock import patch

from scripts import account_connection_pool_v1 as subject


class TransactionPool:
    """Fake transport using real SQLite transactions and a serialized checkout."""
    instances = []

    def __init__(self, **options):
        self.options = options
        self.closed = True
        self.opens = []
        self.closes = []
        self.checkouts = []
        self.checked = 0
        self.connection_lock = threading.Lock()
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
        self.db.execute("CREATE TABLE writes(value TEXT)")
        self.instances.append(self)

    def open(self, **options):
        self.opens.append(options)
        self.closed = False

    def close(self, **options):
        self.closes.append(options)
        self.closed = True

    @staticmethod
    def check_connection(db):
        db.execute("SELECT 1").fetchone()

    @contextmanager
    def connection(self, *, timeout):
        if self.closed:
            raise RuntimeError("Pool closed")
        with self.connection_lock:
            self.checkouts.append(timeout)
            self.options["check"](self.db)
            self.checked += 1
            with self.db:
                yield self.db


class IdentityPoolTests(unittest.TestCase):
    def setUp(self):
        subject.close_pool()
        TransactionPool.instances = []
        self.env = patch.dict(os.environ, {"EIS223_IDENTITY_DATABASE_URL": "postgresql://identity-one/test"})
        self.env.start()
        self.factory = patch("psycopg_pool.ConnectionPool", TransactionPool)
        self.factory.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.factory.stop)
        self.addCleanup(subject.close_pool)

    def tearDown(self):
        # SQLite connections outlive the fake pool's close() so transaction
        # results can be asserted after a checkout, then are released here.
        for pool in TransactionPool.instances:
            pool.db.close()

    def test_lazy_bounded_pool_reuses_transport_and_commits_each_context(self):
        context = subject.connect()
        self.assertEqual(TransactionPool.instances, [])
        with context as first:
            first.execute("INSERT INTO writes VALUES ('first')")
        with subject.connect() as second:
            self.assertIs(first, second)
            self.assertEqual(second.execute("SELECT value FROM writes").fetchall(), [("first",)])
            second.execute("INSERT INTO writes VALUES ('second')")
        pool, = TransactionPool.instances
        self.assertEqual(pool.db.execute("SELECT COUNT(*) FROM writes").fetchone(), (2,))
        self.assertFalse(pool.db.in_transaction)
        self.assertEqual(pool.options["min_size"], 1)
        self.assertEqual(pool.options["max_size"], 8)
        self.assertEqual(pool.options["max_waiting"], 64)
        self.assertEqual(pool.options["kwargs"], {"connect_timeout": 5})
        self.assertEqual(pool.opens, [{"wait": False}])
        self.assertEqual(pool.checkouts, [5.0, 5.0])
        self.assertEqual(pool.checked, 2)
        self.assertFalse(pool.closed)

    def test_exception_rolls_back_before_connection_is_reused(self):
        error = ValueError("route failed")
        with self.assertRaises(ValueError) as raised:
            with subject.connect() as db:
                db.execute("INSERT INTO writes VALUES ('must roll back')")
                raise error
        self.assertIs(raised.exception, error)
        with subject.connect() as next_db:
            self.assertEqual(next_db.execute("SELECT COUNT(*) FROM writes").fetchone(), (0,))
            next_db.execute("INSERT INTO writes VALUES ('recovered')")
        self.assertEqual(next_db.execute("SELECT value FROM writes").fetchall(), [("recovered",)])
        self.assertEqual(len(TransactionPool.instances), 1)

    def test_concurrent_first_requests_create_only_one_pool(self):
        barrier = threading.Barrier(16)

        def request(index):
            barrier.wait(timeout=5)
            with subject.connect() as db:
                db.execute("INSERT INTO writes VALUES (?)", (str(index),))

        with ThreadPoolExecutor(max_workers=16) as executor:
            list(executor.map(request, range(16)))
        pool, = TransactionPool.instances
        self.assertEqual(pool.db.execute("SELECT COUNT(*) FROM writes").fetchone(), (16,))
        self.assertEqual(len(pool.checkouts), 16)

    def test_dsn_rotation_retires_old_pool_without_reusing_its_database(self):
        with subject.connect() as db:
            db.execute("INSERT INTO writes VALUES ('old database')")
        first = TransactionPool.instances[0]
        os.environ["EIS223_IDENTITY_DATABASE_URL"] = "postgresql://identity-two/test"
        with subject.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM writes").fetchone(), (0,))
        self.assertEqual(first.closes, [{"timeout": 0}])
        self.assertEqual(len(TransactionPool.instances), 2)
        self.assertEqual(TransactionPool.instances[1].options["conninfo"], os.environ["EIS223_IDENTITY_DATABASE_URL"])

    def test_new_pid_never_borrows_or_closes_parent_pool(self):
        with subject.connect():
            pass
        parent = TransactionPool.instances[0]
        with patch.object(subject.os, "getpid", return_value=os.getpid() + 1):
            with subject.connect() as db:
                self.assertIsNot(db, parent.db)
            subject.close_pool()
        self.assertEqual(parent.closes, [])
        self.assertEqual(len(TransactionPool.instances), 2)
        self.assertEqual(TransactionPool.instances[1].closes, [{"timeout": 5}])
        subject._inherited_pools.remove(parent)

    def test_shutdown_closes_pool_and_next_use_can_initialize_again(self):
        with subject.connect():
            pass
        first = TransactionPool.instances[0]
        subject.close_pool()
        subject.close_pool()
        self.assertEqual(first.closes, [{"timeout": 5}])
        with subject.connect():
            pass
        self.assertEqual(len(TransactionPool.instances), 2)

    def test_missing_dsn_and_pool_failures_propagate_without_direct_fallback(self):
        with patch.dict(os.environ, {"EIS223_IDENTITY_DATABASE_URL": ""}):
            with self.assertRaisesRegex(RuntimeError, "not configured"):
                with subject.connect():
                    self.fail("A missing identity database must not be accepted")
        self.assertEqual(TransactionPool.instances, [])
        with patch.object(TransactionPool, "open", side_effect=RuntimeError("bad configuration")):
            with self.assertRaisesRegex(RuntimeError, "bad configuration"):
                with subject.connect():
                    self.fail("Pool startup errors must not be ignored")
        self.assertEqual(TransactionPool.instances[0].closes, [{"timeout": 0}])
        self.assertIsNone(subject._pool)
        with subject.connect():
            pass
        self.assertEqual(len(TransactionPool.instances), 2)


if __name__ == "__main__":
    unittest.main()
