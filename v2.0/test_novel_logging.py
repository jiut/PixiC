import io
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import test_novel
from log_record import logger
from novel_epub import build_epub
from PIL import Image


class NovelLoggingTests(unittest.TestCase):
    def setUp(self):
        test_novel.NovelTests.setUp(self)
        self.records = []
        sink = logger.add(lambda msg: self.records.append(msg.record), level='DEBUG')
        self.addCleanup(logger.remove, sink)

    def messages(self):
        return '\n'.join(record['message'] for record in self.records)

    def test_api_error_reason_and_retry_visible(self):
        self.novel.rest_list = ['show']
        self.downloader.baseRequest.return_value = SimpleNamespace(
            text='{"error": true, "message": "LOGIN_REQUIRED"}')
        with patch('novel.time.sleep'):
            stats = self.novel.run()
        self.assertEqual(stats['page_failed'], 1)
        self.assertIn('LOGIN_REQUIRED', self.messages())
        self.assertIn('重试 1/3', self.messages())
        self.assertIn('未完整完成', self.records[-1]['message'])
        self.assertEqual(self.records[-1]['level'].name, 'WARNING')

    def test_invalid_total_does_not_stop_private_or_next_run(self):
        self.novel.rest_list = ['show', 'hide']
        self.novel.request = Mock(side_effect=[
            {'works': [{'id': '1'}], 'total': None},
            {'works': [{'id': '2'}], 'total': 1},
            {'works': [], 'total': 0}, {'works': [], 'total': 0}])
        self.novel.save_novel = Mock()
        stats = self.novel.run()
        self.assertEqual(stats['page_failed'], 1)
        self.assertEqual(stats['success'], 1)
        self.novel.save_novel.assert_called_once_with(2)
        self.assertEqual(self.novel.run()['page_failed'], 0)

    def test_malformed_work_and_save_failure_summary(self):
        self.novel.rest_list = ['show']
        self.novel.request = Mock(return_value={'works': [None, {'id': '1'}, {'id': '2'}], 'total': 3})
        self.novel.save_novel = Mock(side_effect=[OSError('disk full'), None])
        stats = self.novel.run()
        self.assertEqual(stats['failed'], 2)
        self.assertEqual(stats['success'], 1)
        self.assertIn('disk full', self.messages())
        self.assertIn('作品失败 2 次', self.records[-1]['message'])

    def test_unexpected_error_records_exception_and_allows_next_round(self):
        with patch.object(self.novel, '_run', side_effect=RuntimeError('unexpected')):
            self.assertTrue(self.novel.run()['aborted'])
        exceptions = [record['exception'] for record in self.records if record['exception']]
        self.assertEqual(exceptions[0].type, RuntimeError)
        self.novel.rest_list = ['show']
        self.novel.request = Mock(return_value={'works': [], 'total': 0})
        self.assertFalse(self.novel.run()['aborted'])
        self.assertEqual(self.records[-1]['level'].name, 'INFO')

    def test_database_failure_not_reported_as_success(self):
        self.novel.rest_list = ['show']
        self.db.ensure_novel_table.side_effect = RuntimeError('database unavailable')
        stats = self.novel.run()
        self.assertTrue(stats['aborted'])
        self.assertIn('database unavailable', self.messages())
        self.assertIn('未完整完成', self.records[-1]['message'])

    def test_unchanged_epub_and_image_retry_logs(self):
        self.novel.request = Mock(return_value=self.body)
        self.novel.save_novel(123)
        self.novel.save_novel(123)
        self.assertIn('EPUB 内容未变', self.messages())
        self.downloader.baseRequest.return_value = SimpleNamespace(content=b'not an image')
        with patch('novel.time.sleep'):
            with self.assertRaisesRegex(RuntimeError, 'cannot identify image'):
                self.novel.download_image('https://example.org/image.png')
        self.assertIn('小说图片重试 2/3', self.messages())

    def test_image_progress_callback(self):
        image = io.BytesIO()
        Image.new('RGB', (1, 1)).save(image, format='PNG')
        self.body['coverUrl'] = 'https://example.org/cover.png'
        progress = Mock()
        build_epub(self.body, Mock(), Mock(return_value=image.getvalue()), progress=progress)
        progress.assert_called_once_with(1, 1, self.body['coverUrl'])

    def test_scheduler_logs_sleep_after_run(self):
        from scheduler import Scheduler
        with patch('novel.Novel', return_value=self.novel), \
                patch.object(self.novel, 'run_once') as run, \
                patch('scheduler.time.sleep') as sleep:
            Scheduler().scheduler_novel(limit=15)
        run.assert_called_once()
        sleep.assert_not_called()


if __name__ == '__main__':
    unittest.main()
