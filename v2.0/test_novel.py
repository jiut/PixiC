import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from novel import Novel


class NovelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = SimpleNamespace(pool=True, upsert_novel=Mock(), ensure_novel_table=Mock(),
                                  ensure_novel_unavailable_table=Mock(),
                                  mark_novel_unavailable=Mock(),
                                  is_novel_unavailable=Mock(return_value=False))
        self.db.check_illust = Mock(return_value=(False, ''))
        self.db.select_latest_novels = Mock(return_value=[])
        self.downloader = SimpleNamespace(db=self.db, client=SimpleNamespace(user_id='42'), baseRequest=Mock())
        self.novel = Novel(self.downloader, self.temp.name)
        self.novel.check_update = Mock(return_value=True)
        self.novel.integrity_check = Mock(return_value={'failed': 0})
        self.body = {'id': '123', 'userId': '9', 'userName': '作者', 'title': '标题😀',
                     'content': '正文\n[newpage]\n[ruby:漢字 > かんじ]',
                     'tags': {'tags': [{'tag': '测试'}]}, 'xRestrict': 2}

    def test_save_and_restore_missing_file(self):
        self.novel.request = Mock(return_value=self.body)
        info = self.novel.save_novel(123)
        path = Path(info['path'])
        self.assertIn(self.body['content'], path.with_suffix('.txt').read_text(encoding='utf-8'))
        self.assertEqual(info['is_r18'], 1)
        self.assertEqual(json.loads((path.parent / 'metadata.json').read_text(encoding='utf-8')), self.body)
        before = path.stat().st_mtime_ns
        self.novel.save_novel(123)
        self.assertEqual(before, path.stat().st_mtime_ns)
        path.unlink()
        self.novel.save_novel(123)
        self.assertTrue(path.is_file())
        self.assertEqual(self.db.upsert_novel.call_count, 3)

    def test_write_failure_does_not_record_success(self):
        self.novel.request = Mock(return_value=self.body)
        with patch('novel.os.replace', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.novel.save_novel(123)
        self.db.upsert_novel.assert_not_called()
        self.assertEqual(list(Path(self.temp.name).rglob('*.tmp')), [])

    def test_missing_content_does_not_overwrite_archive(self):
        self.novel.request = Mock(return_value=self.body)
        info = self.novel.save_novel(123)
        before = Path(info['path']).read_bytes()
        del self.body['content']
        with self.assertRaises(ValueError):
            self.novel.save_novel(123)
        self.assertEqual(Path(info['path']).read_bytes(), before)
        self.assertEqual(self.db.upsert_novel.call_count, 1)

    def test_public_and_private_pagination(self):
        self.novel.rest_list = ['show', 'hide']
        self.novel.page_size = 1
        self.novel.request = Mock(side_effect=[
            {'works': [{'id': '1'}], 'total': 2},
            {'works': [{'id': '2'}], 'total': 2},
            {'works': [{'id': '3'}], 'total': 1}])
        self.novel.save_novel = Mock()
        self.novel.run()
        self.assertEqual([call.args[0] for call in self.novel.save_novel.call_args_list], [1, 2, 3])
        params = [call.args[1] for call in self.novel.request.call_args_list]
        self.assertEqual([(p['rest'], p['offset']) for p in params], [('show', 0), ('show', 1), ('hide', 0)])

    def test_bounded_retry(self):
        self.downloader.baseRequest.return_value = SimpleNamespace(text='{"error": true, "message": "fail"}')
        with patch('novel.time.sleep'):
            with self.assertRaises(RuntimeError):
                self.novel.request('https://www.pixiv.net/ajax/novel/123')
        self.assertEqual(self.downloader.baseRequest.call_count, 3)

    def test_failure_does_not_block_remaining_works(self):
        self.novel.rest_list = ['show']
        self.novel.request = Mock(return_value={'works': [{'id': '1'}, {'id': '2'}], 'total': 2})
        self.novel.save_novel = Mock(side_effect=[RuntimeError('unavailable'), None])
        self.novel.run()
        self.assertEqual(self.novel.save_novel.call_count, 2)


if __name__ == '__main__':
    unittest.main()
