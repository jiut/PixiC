import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from checker import run_startup_check
from db import db_client
from message import TEMP_MSG


class _FailingCursor:
    def execute(self, *args, **kwargs):
        raise RuntimeError("database unavailable")

    def close(self):
        pass


class _FakeConnection:
    def cursor(self, *_args, **_kwargs):
        return _FailingCursor()

    def close(self):
        pass


class CheckerFailureTests(unittest.TestCase):
    def test_unavailable_detail_is_counted_without_repair_failure(self):
        db = SimpleNamespace(
            pool=True,
            select_latest_records=Mock(return_value=[{
                "pid": 123, "path": "missing", "pageCount": 1,
                "illustType": 0, "uid": 1, "userName": "user",
            }]),
        )
        downloader = SimpleNamespace(
            db=db,
            get_illust_info=Mock(return_value=TEMP_MSG["PID_DELETED_TEXT"]),
        )
        with patch("checker.TABLES", ["bookmark"]):
            summary = run_startup_check(downloader)
        self.assertEqual(summary["unavailable"], 1)
        self.assertEqual(summary["failed"], 0)
        self.assertEqual(summary["repaired"], 0)

    def test_database_strict_queries_propagate_errors(self):
        client = db_client.__new__(db_client)
        client.get_conn = Mock(return_value=(_FakeConnection(), _FailingCursor()))
        with self.assertRaises(RuntimeError):
            client.check_illust(123, strict=True)
        with self.assertRaises(RuntimeError):
            client.select_latest_records(strict=True)


if __name__ == "__main__":
    unittest.main()
