import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from novel import Novel


class NovelFlowTests(unittest.TestCase):
    def make(self, db=True):
        database = SimpleNamespace(
            pool=True if db else None,
            check_illust=Mock(return_value=(True, '')),
            select_latest_novels=Mock(return_value=[]),
            ensure_novel_table=Mock(),
            ensure_novel_unavailable_table=Mock(),
            is_novel_unavailable=Mock(return_value=False),
            mark_novel_unavailable=Mock(),
            upsert_novel=Mock(),
        )
        if not db:
            del database.pool
        downloader = SimpleNamespace(db=database, client=SimpleNamespace(user_id='42'), baseRequest=Mock())
        return Novel(downloader, Path.cwd() / 'novel-flow-test'), database

    def test_unchanged_bookmarks_skip_detail_requests(self):
        novel, db = self.make()
        novel.rest_list = ['show']
        novel.request = Mock(return_value={'works': [{'id': '10'}], 'total': 1})
        novel.save_novel = Mock()
        with patch('novel.BOOKMARK_HIDE_ENABLE', False):
            stats = novel.run()
        self.assertTrue(stats['skipped'])
        novel.save_novel.assert_not_called()
        self.assertEqual(novel.request.call_count, 1)
        db.check_illust.assert_called_once_with(10, key='nid', table='novel', strict=True)

    def test_unchanged_bookmarks_use_warning_level(self):
        novel, _ = self.make()
        novel.rest_list = ['show']
        novel.request = Mock(return_value={'works': [{'id': '10'}], 'total': 1})
        with patch('novel.BOOKMARK_HIDE_ENABLE', False), \
             patch('novel.logger') as log:
            self.assertEqual(novel.check_update(), 'unchanged')
        log.warning.assert_called_once_with('Novel: 收藏未变化，暂不更新')
        log.info.assert_not_called()

    def test_new_bookmark_starts_full_pagination(self):
        novel, db = self.make()
        novel.rest_list = ['show']
        novel.page_size = 2
        db.check_illust.return_value = (False, '')
        novel.request = Mock(side_effect=[
            {'works': [{'id': '10'}], 'total': 1},
            {'works': [{'id': '10'}, {'id': '11'}], 'total': 3},
            {'works': [{'id': '12'}], 'total': 3},
        ])
        novel.save_novel = Mock()
        with patch('novel.BOOKMARK_HIDE_ENABLE', False):
            stats = novel.run()
        self.assertFalse(stats['skipped'])
        self.assertEqual([call.args[0] for call in novel.save_novel.call_args_list], [10, 11, 12])

    def test_existing_novels_are_skipped_after_new_bookmark(self):
        novel, db = self.make()
        novel.rest_list = ['show']
        novel.page_size = 2
        db.check_illust.side_effect = [(False, ''), (False, ''), (True, ''), (True, '')]
        novel.request = Mock(side_effect=[
            {'works': [{'id': '10'}], 'total': 1},
            {'works': [{'id': '10'}, {'id': '11'}], 'total': 2},
        ])
        novel.save_novel = Mock()
        with patch('novel.BOOKMARK_HIDE_ENABLE', False):
            stats = novel.run()
        self.assertEqual([call.args[0] for call in novel.save_novel.call_args_list], [10])
        self.assertEqual(stats['skipped_existing'], 1)

    def test_private_bookmark_check_failure_is_not_unchanged(self):
        novel, _ = self.make()
        with patch('novel.BOOKMARK_HIDE_ENABLE', True):
            novel._bookmark_page = Mock(side_effect=[[], RuntimeError('private failed')])
            self.assertEqual(novel.check_update(), 'error')

    def test_unavailable_bookmark_does_not_trigger_update(self):
        novel, db = self.make()
        db.is_novel_unavailable.return_value = True
        with patch('novel.BOOKMARK_HIDE_ENABLE', False):
            novel._bookmark_page = Mock(return_value=[{'id': '10'}])
            self.assertEqual(novel.check_update(), 'unchanged')

    def test_epub_references_are_checked(self):
        import io
        import zipfile
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w') as zf:
            zf.writestr('mimetype', b'application/epub+zip')
            zf.writestr('META-INF/container.xml', b'<container/>')
            zf.writestr('OEBPS/content.opf', b'<package/>')
            zf.writestr('OEBPS/nav.xhtml', b'<html/>')
            zf.writestr('OEBPS/text.xhtml', b'<html><img src="images/missing.png"/></html>')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'broken.epub'
            path.write_bytes(archive.getvalue())
            self.assertFalse(Novel.is_valid_epub(path))

    def test_recent_twenty_integrity_is_summarized(self):
        novel, db = self.make()
        db.select_latest_novels.return_value = [
            {'nid': 10, 'path': str(Path.cwd() / 'missing.epub'), 'title': 'missing'}]
        novel.save_novel = Mock()
        stats = novel.integrity_check()
        self.assertEqual(stats['total'], 1)
        self.assertEqual(stats['repaired'], 1)
        self.assertEqual(stats['failed'], 0)
        novel.save_novel.assert_called_once_with(10, emit_success=False)

    def test_valid_generated_epub_and_missing_manifest_resource(self):
        import io
        import zipfile
        from novel_epub import build_epub
        body = {'id': '1', 'title': 'Test', 'userName': 'Author',
                'content': '[chapter:One]Text[newpage][jump:1]'}
        data = build_epub(body, Mock(), Mock())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'test.epub'
            path.write_bytes(data)
            self.assertTrue(Novel.is_valid_epub(path))
            broken = io.BytesIO()
            with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(broken, 'w') as dest:
                for name in source.namelist():
                    content = source.read(name)
                    if name == 'OEBPS/content.opf':
                        content = content.replace(b'href="text.xhtml"', b'href="missing.xhtml"')
                    dest.writestr(name, content)
            path.write_bytes(broken.getvalue())
            self.assertFalse(Novel.is_valid_epub(path))

    def test_epub_navigation_and_xml_validation(self):
        import io
        import zipfile
        from novel_epub import build_epub
        data = build_epub({'id': '1', 'title': 'Test', 'userName': 'Author',
                           'content': '[chapter:One]Text'}, Mock(), Mock())
        for target, replacement in [('OEBPS/nav.xhtml', b'chapter-missing'),
                                     ('OEBPS/text.xhtml', b'<broken')]:
            broken = io.BytesIO()
            with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(broken, 'w') as dest:
                for name in source.namelist():
                    content = source.read(name)
                    if name == target:
                        content = (content.replace(b'chapter-1', replacement)
                                   if target.endswith('nav.xhtml') else replacement)
                    dest.writestr(name, content)
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'test.epub'
                path.write_bytes(broken.getvalue())
                self.assertFalse(Novel.is_valid_epub(path))

    def test_integrity_failure_is_not_reported_as_completed(self):
        novel, db = self.make()
        novel.integrity_check = Mock(return_value={'failed': 1})
        novel.check_update = Mock(return_value=False)
        stats = novel.run()
        self.assertEqual(stats['integrity_failed'], 1)

    def test_integrity_query_failure_is_reported(self):
        novel, db = self.make()
        db.select_latest_novels.side_effect = RuntimeError('database unavailable')
        stats = novel.integrity_check()
        self.assertEqual(stats['query_failed'], 1)

    def test_integrity_404_is_classified_as_deleted(self):
        from novel import NovelNotFoundError
        novel, db = self.make()
        db.select_latest_novels.return_value = [
            {'nid': 10, 'path': str(Path.cwd() / 'missing.epub'), 'title': 'missing'}]
        novel.save_novel = Mock(side_effect=NovelNotFoundError('deleted'))
        stats = novel.integrity_check()
        self.assertEqual(stats['deleted'], 1)
        db.mark_novel_unavailable.assert_called_once()
        self.assertEqual(db.mark_novel_unavailable.call_args.args[0], 10)

    def test_unavailable_table_failure_aborts_round(self):
        novel, db = self.make()
        db.ensure_novel_unavailable_table.side_effect = RuntimeError('schema unavailable')
        stats = novel.run()
        self.assertTrue(stats['aborted'])

    def test_pagination_failure_is_incomplete(self):
        novel, _ = self.make()
        novel.rest_list = ['show']
        novel.check_update = Mock(return_value='changed')
        novel.request = Mock(side_effect=RuntimeError('page failed'))
        stats = novel.run()
        self.assertEqual(stats['page_failed'], 1)
        self.assertFalse(stats['skipped'])

    def test_database_disabled_keeps_full_update(self):
        novel, _ = self.make(db=False)
        novel.rest_list = ['show']
        novel.request = Mock(side_effect=[
            {'works': [{'id': '10'}], 'total': 1},
            {'works': []},
        ])
        novel.save_novel = Mock()
        stats = novel.run()
        self.assertFalse(stats['skipped'])
        novel.save_novel.assert_called_once_with(10)


if __name__ == '__main__':
    unittest.main()
