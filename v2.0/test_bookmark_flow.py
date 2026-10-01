import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bookmark import Bookmark


class BookmarkFlowTests(unittest.TestCase):
    def make(self):
        worker = Bookmark.__new__(Bookmark)
        worker.Downloader = SimpleNamespace()
        worker.db = SimpleNamespace()
        worker.file_manager = SimpleNamespace()
        worker.rest_list = ['show']
        worker.rest_dict = {'show': '公开'}
        worker.class_name = 'Bookmark'
        worker.bookmark_page_offset = 48
        worker.day_count = 5
        worker.day_all_update_num = 5
        worker.day_limit = 800
        worker.check_update = Mock(return_value=True)
        return worker

    def test_page_failure_does_not_spin(self):
        worker = self.make()
        worker.get_page_bookmark = Mock(return_value=None)
        with patch('bookmark.run_startup_check'):
            worker.run()
        worker.get_page_bookmark.assert_called_once_with(0, 'show')


if __name__ == '__main__':
    unittest.main()
