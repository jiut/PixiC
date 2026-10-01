import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import test_novel
from log_record import logger
from message import TEMP_MSG
from novel import NovelNotFoundError


class Novel404Tests(unittest.TestCase):
    setUp = test_novel.NovelTests.setUp

    def test_404_skips_without_retry_and_continues(self):
        self.novel.rest_list = ['show']
        self.body['id'] = '2'
        self.downloader.baseRequest.side_effect = [
            SimpleNamespace(status_code=200, text=json.dumps({'body': {
                'works': [{'id': '1'}, {'id': '2'}], 'total': 2}})),
            SimpleNamespace(status_code=404, text='<html>Not Found</html>'),
            SimpleNamespace(status_code=200, text=json.dumps({'body': self.body})),
        ]
        messages = []
        sink = logger.add(lambda msg: messages.append(msg.record['message']))
        try:
            with patch('novel.time.sleep') as sleep:
                stats = self.novel.run()
            sleep.assert_not_called()
        finally:
            logger.remove(sink)
        self.assertEqual(self.downloader.baseRequest.call_count, 3)
        self.assertEqual(stats['deleted'], 1)
        self.assertEqual(stats['failed'], 0)
        self.assertEqual(stats['success'], 1)
        self.assertEqual(sum(TEMP_MSG['PID_DELETED_TEXT'] in msg for msg in messages), 1)
        self.assertFalse(any('重试' in msg or '小说抓取失败' in msg for msg in messages))
        self.db.upsert_novel.assert_called_once()
        self.db.mark_novel_unavailable.assert_called_once_with(1, TEMP_MSG['PID_DELETED_TEXT'])

    def test_other_404_endpoints_keep_retry_behavior(self):
        self.downloader.baseRequest.return_value = SimpleNamespace(status_code=404, text='not json')
        with patch('novel.time.sleep'):
            with self.assertRaises(RuntimeError) as error:
                self.novel.request('https://www.pixiv.net/ajax/novel/123/insert_illusts')
        self.assertNotIsInstance(error.exception, NovelNotFoundError)
        self.assertEqual(self.downloader.baseRequest.call_count, 3)

    def test_downloader_preserves_detail_404_without_warning(self):
        from downer import Downloader
        downloader = Downloader.__new__(Downloader)
        downloader.headers = {}
        downloader.cookie_list = [{}]
        downloader._adaptive_delay = 0
        response = SimpleNamespace(status_code=404, text='not json')
        downloader.se = SimpleNamespace(get=Mock(return_value=response))
        with patch('downer.logger') as log:
            result = downloader.baseRequest({'url': 'https://www.pixiv.net/ajax/novel/123'})
        self.assertIs(result, response)
        log.warning.assert_not_called()
        downloader.se.get.assert_called_once()


if __name__ == '__main__':
    unittest.main()
