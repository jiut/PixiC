import unittest
import multiprocessing
import queue
from types import SimpleNamespace

# The production logger uses a multiprocessing queue; use an in-process queue
# for unit tests running in restricted Windows environments.
multiprocessing.SimpleQueue = queue.SimpleQueue

from db import db_client


class _Cursor:
    def __init__(self, fetch_values=None):
        self.fetch_values = list(fetch_values or [])
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchone(self):
        return self.fetch_values.pop(0) if self.fetch_values else None

    def close(self):
        pass


class NovelUnavailableDbTests(unittest.TestCase):
    def test_create_mark_and_query_unavailable_state(self):
        cursor = _Cursor([None, {'1': 1}])
        connection = SimpleNamespace(commit=lambda: None, rollback=lambda: None,
                                     close=lambda: None)
        db = db_client.__new__(db_client)
        db.get_conn = lambda: (connection, cursor)

        db.ensure_novel_unavailable_table()
        db.mark_novel_unavailable(123, 'deleted')
        self.assertTrue(db.is_novel_unavailable(123))
        self.assertTrue(any('novel_unavailable' in sql for sql, _ in cursor.calls))
        self.assertTrue(any('ON DUPLICATE KEY UPDATE' in sql for sql, _ in cursor.calls))


if __name__ == '__main__':
    unittest.main()
