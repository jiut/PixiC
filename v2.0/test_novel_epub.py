import io
import unittest
import zipfile
from xml.etree import ElementTree as ET
from unittest.mock import Mock
from PIL import Image
from novel_epub import build_epub
import test_novel


class EpubTests(unittest.TestCase):
    def setUp(self):
        image = io.BytesIO()
        Image.new('RGB', (8, 8), 'green').save(image, format='PNG')
        self.png = image.getvalue()
        self.body = {'id': '123', 'title': '测试 < & 标题', 'userName': '作者 &',
                     'content': '[chapter:开始]正文 < &\n[[rb:漢字 > かんじ]]'
                     '[uploadedimage:9][pixivimage:9-2][uploadedimage:9]'
                     '[newpage][jump:1][[jumpuri:链接>https://example.org/?a=1&b=2]]',
                     'textEmbeddedImages': {'9': {'urls': {'original': 'https://i.pximg.net/upload.png'}}},
                     'coverUrl': 'https://i.pximg.net/cover.png'}
        self.request = Mock(return_value={'9-2': {'illust': {'images': {'original': 'https://i.pximg.net/ref.png'}}}})
        self.download = Mock(return_value=self.png)

    def test_epub_structure_and_embedded_resources(self):
        data = build_epub(self.body, self.request, self.download)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(archive.namelist()[0], 'mimetype')
            self.assertEqual(archive.getinfo('mimetype').compress_type, zipfile.ZIP_STORED)
            self.assertEqual(archive.read('mimetype'), b'application/epub+zip')
            for name in archive.namelist():
                if name.endswith(('.xml', '.opf', '.xhtml')):
                    ET.fromstring(archive.read(name))
            opf = ET.fromstring(archive.read('OEBPS/content.opf'))
            ns = {'opf': 'http://www.idpf.org/2007/opf'}
            manifest = {item.attrib['id']: item for item in opf.findall('opf:manifest/opf:item', ns)}
            for item in manifest.values():
                self.assertIn('OEBPS/' + item.attrib['href'], archive.namelist())
            for item in opf.findall('opf:spine/opf:itemref', ns):
                self.assertIn(item.attrib['idref'], manifest)
            text = ET.fromstring(archive.read('OEBPS/text.xhtml'))
            images = text.findall('.//{http://www.w3.org/1999/xhtml}img')
            self.assertEqual(len(images), 3)
            self.assertEqual(images[0].attrib['src'], images[2].attrib['src'])
            for img in images:
                self.assertEqual(archive.read('OEBPS/' + img.attrib['src']), self.png)
            self.assertIn(b'<ruby>', archive.read('OEBPS/text.xhtml'))
            self.assertIn(b'chapter-1', archive.read('OEBPS/nav.xhtml'))
        self.assertEqual(self.download.call_count, 3)
        self.assertEqual(self.request.call_args.args[1], [('id[]', '9-2')])
        self.assertEqual(data, build_epub(self.body, self.request, self.download))

    def test_unavailable_image_fails(self):
        self.request.return_value = {'9-2': {'illust': None}}
        with self.assertRaises(ValueError):
            build_epub(self.body, self.request, self.download)

    def test_html_error_is_not_packaged_as_image(self):
        self.download.return_value = b'<html>error</html>'
        with self.assertRaises(OSError):
            build_epub(self.body, self.request, self.download)


class EpubFailureTests(unittest.TestCase):
    setUp = test_novel.NovelTests.setUp

    def test_image_failure_preserves_previous_epub_and_db(self):
        self.novel.request = Mock(return_value=self.body)
        info = self.novel.save_novel(123)
        from pathlib import Path
        path = Path(info['path'])
        previous = path.read_bytes()
        self.body['content'] = '[uploadedimage:9]'
        self.body['textEmbeddedImages'] = {'9': {'urls': {'original': 'https://i.pximg.net/test.png'}}}
        self.novel.download_image = Mock(side_effect=RuntimeError('network failure'))
        with self.assertRaises(RuntimeError):
            self.novel.save_novel(123)
        self.assertEqual(path.read_bytes(), previous)
        self.assertEqual(self.db.upsert_novel.call_count, 1)


if __name__ == '__main__':
    unittest.main()
