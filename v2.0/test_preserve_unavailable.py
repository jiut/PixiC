import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch


class PreserveUnavailableTests(unittest.TestCase):
    def make_worker(self, module, class_name, table=None):
        worker = getattr(module, class_name).__new__(getattr(module, class_name))
        worker.class_name = class_name
        worker.db = SimpleNamespace(
            pool=True,
            check_illust=Mock(return_value=(True, 'old/path')),
            delete_user_illust=Mock(return_value=True),
            update_illust=Mock(),
        )
        worker.Downloader = SimpleNamespace(get_illust_info=Mock(return_value=module.TEMP_MSG['PID_DELETED_TEXT']))
        worker.file_manager = SimpleNamespace(search_isExistsPid=Mock(return_value=False))
        return worker

    def test_bookmark_404_preserves_record_and_file(self):
        import bookmark
        worker = self.make_worker(bookmark, 'Bookmark')
        with patch.object(bookmark, 'SKIP_DB_EXISTS_ILLUST', False), \
                patch.object(bookmark, 'SKIP_ISEXISTS_ILLUST', False):
            result = worker.thread_by_illust(123)
        self.assertEqual(result, bookmark.TEMP_MSG['PID_DELETED_TEXT'])
        worker.db.delete_user_illust.assert_not_called()
        worker.db.update_illust.assert_not_called()

    def test_crawler_404_preserves_record_and_file(self):
        import crawler
        worker = self.make_worker(crawler, 'Crawler')
        with patch.object(crawler, 'SKIP_DB_EXISTS_ILLUST', False), \
                patch.object(crawler, 'SKIP_ISEXISTS_ILLUST', False):
            result = worker.thread_by_illust(123, 456)
        self.assertEqual(result, crawler.TEMP_MSG['PID_DELETED_TEXT'])
        worker.db.delete_user_illust.assert_not_called()
        worker.db.update_illust.assert_not_called()


if __name__ == '__main__':
    unittest.main()
