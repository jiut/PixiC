import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock


def console_worker(kind):
    from log_record import logger
    for i in range(12):
        if kind == 'skip':
            logger.info('SKIP_DB_EXISTS_ILLUST - test-{}', i)
            logger.info('SKIP_DB_EXISTS_NOVEL - test-{}', i)
        else:
            logger.info('NOVEL_LOG_TEST - test-{}', i)


class LogIntegrationTests(unittest.TestCase):
    def test_spawned_console_logs_have_separate_lines(self):
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--probe'],
                                capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(b'\x1b[2K', result.stderr)
        lines = [line for line in result.stderr.splitlines()
                 if any(marker in line for marker in
                        (b'SKIP_DB_EXISTS_ILLUST', b'SKIP_DB_EXISTS_NOVEL', b'NOVEL_LOG_TEST'))]
        self.assertEqual(len(lines), 36)
        for line in lines:
            self.assertEqual(sum(line.count(marker) for marker in
                                 (b'SKIP_DB_EXISTS_ILLUST', b'SKIP_DB_EXISTS_NOVEL', b'NOVEL_LOG_TEST')), 1)

    def test_existing_table_does_not_repeat_ddl(self):
        from db import db_client
        database = db_client.__new__(db_client)
        connection, cursor = Mock(), Mock()
        cursor.fetchone.return_value = {'1': 1}
        database.get_conn = Mock(return_value=(connection, cursor))
        database.ensure_novel_table()
        self.assertEqual(cursor.execute.call_count, 1)
        self.assertIn('information_schema.tables', cursor.execute.call_args.args[0])
        cursor.close.assert_called_once()
        connection.close.assert_called_once()

    def test_upsert_binds_update_values_without_deprecated_function(self):
        from db import db_client
        database = db_client.__new__(db_client)
        connection, cursor = Mock(), Mock()
        database.get_conn = Mock(return_value=(connection, cursor))
        fields = ('nid', 'uid', 'userName', 'purl', 'title', 'description', 'tag',
                  'createDate', 'updateDate', 'viewCount', 'bookmarkCount', 'is_r18', 'path')
        info = dict(zip(fields, range(len(fields))))
        database.upsert_novel(info)
        sql, values = cursor.execute.call_args.args
        self.assertNotIn('=VALUES(', sql)
        self.assertEqual(sql.count('%s'), len(values))
        self.assertEqual(values, tuple(range(13)) + tuple(range(1, 13)))
        connection.commit.assert_called_once()

    def test_unavailable_table_does_not_repeat_ddl(self):
        from db import db_client
        database = db_client.__new__(db_client)
        connection, cursor = Mock(), Mock()
        cursor.fetchone.return_value = {'1': 1}
        database.get_conn = Mock(return_value=(connection, cursor))
        database.ensure_novel_unavailable_table()
        self.assertEqual(cursor.execute.call_count, 1)
        self.assertIn('information_schema.tables', cursor.execute.call_args.args[0])


if __name__ == '__main__':
    if '--probe' in sys.argv:
        from multiprocessing import get_context
        context = get_context('spawn')
        processes = [context.Process(target=console_worker, args=(kind,)) for kind in ('skip', 'novel')]
        for process in processes:
            process.start()
        for process in processes:
            process.join(20)
            if process.is_alive():
                process.terminate()
                process.join()
                sys.exit(1)
            if process.exitcode:
                sys.exit(process.exitcode)
    else:
        unittest.main()
