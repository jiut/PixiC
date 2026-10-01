"""生成包含本地图片资源的 EPUB 3；使用标准库和已有的 Pillow。"""
import io
import re
import zipfile
from datetime import datetime, timezone
from html import escape
from urllib.parse import urlparse

from PIL import Image


def xml(text):
    # XML 1.0 不允许控制字符和孤立代理字符。
    return escape(re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]', '', str(text)))


def image_data(content):
    with Image.open(io.BytesIO(content)) as image:
        fmt = image.format
        image.verify()
    types = {'JPEG': ('jpg', 'image/jpeg'), 'PNG': ('png', 'image/png'),
             'GIF': ('gif', 'image/gif')}
    if fmt not in types:
        with Image.open(io.BytesIO(content)) as image:
            output = io.BytesIO()
            image.convert('RGBA').save(output, format='PNG')
            content = output.getvalue()
        fmt = 'PNG'
    ext, mime = types[fmt]
    return content, ext, mime


def build_epub(body, request, download, progress=None):
    """request 返回 AJAX body；download 返回经验证的图片字节。"""
    nid = str(int(body['id']))
    content = body['content']
    flags = list(dict.fromkeys(re.findall(r'\[(uploadedimage|pixivimage):(\d+(?:-\d+)?)\]', content)))
    refs = [key for kind, key in flags if kind == 'pixivimage']
    referenced = {}
    for start in range(0, len(refs), 20):
        referenced.update(request('https://www.pixiv.net/ajax/novel/{}/insert_illusts'.format(nid),
                                  [('id[]', key) for key in refs[start:start + 20]]))
    uploaded = body.get('textEmbeddedImages') or {}
    resources = []
    by_url = {}
    image_total = len(flags) + bool(body.get('coverUrl'))
    image_current = 0

    def asset(url):
        nonlocal image_current
        image_current += 1
        if progress:
            progress(image_current, image_total, url)
        if url not in by_url:
            data, ext, mime = image_data(download(url))
            name = 'images/image-{}.{}'.format(len(resources), ext)
            resources.append((name, mime, data))
            by_url[url] = name
        return by_url[url]

    images = {}
    for kind, key in flags:
        if kind == 'uploadedimage':
            url = (uploaded.get(key) or {}).get('urls', {}).get('original')
        else:
            illust = (referenced.get(key) or {}).get('illust') or {}
            url = (illust.get('images') or {}).get('original')
        if not url:
            # 无法获得图片时不生成缺图的成功归档，下轮重新尝试。
            raise ValueError('内嵌图片不可用: {}:{}'.format(kind, key))
        images[(kind, key)] = asset(url)
    cover = asset(body['coverUrl']) if body.get('coverUrl') else None
    headings = []
    page = 1
    pattern = re.compile(r'\[\[(?:rb|jumpuri):.*?\]\]|\[(?:uploadedimage|pixivimage|chapter|jump|ruby):[^\]]*\]|\[newpage\]', re.S)
    parts = ['<div id="page-1">']
    end = 0
    for match in pattern.finditer(content):
        parts.append(xml(content[end:match.start()]).replace('\n', '<br/>'))
        token = match.group()
        if token == '[newpage]':
            page += 1
            parts.append('</div><div class="page" id="page-{}">'.format(page))
        elif token.startswith('[chapter:'):
            title = token[9:-1].strip()
            anchor = 'chapter-{}'.format(len(headings) + 1)
            headings.append((anchor, title))
            parts.append('<h2 id="{}">{}</h2>'.format(anchor, xml(title)))
        elif token.startswith(('[uploadedimage:', '[pixivimage:')):
            key = tuple(token[1:-1].split(':', 1))
            parts.append('<div class="illustration"><img src="{}" alt="{}"/></div>'.format(images[key], xml(token)))
        elif token.startswith(('[ruby:', '[[rb:')):
            value = token[6:-1] if token.startswith('[ruby:') else token[5:-2]
            if '>' in value:
                base, reading = value.split('>', 1)
                parts.append('<ruby>{}<rp>（</rp><rt>{}</rt><rp>）</rp></ruby>'.format(xml(base.strip()), xml(reading.strip())))
            else:
                parts.append(xml(token))
        elif token.startswith('[[jumpuri:'):
            value = token[10:-2]
            label, sep, url = value.partition('>')
            url = url.strip()
            if sep and urlparse(url).scheme in ('http', 'https'):
                parts.append('<a href="{}">{}</a>'.format(xml(url), xml(label.strip())))
            else:
                parts.append(xml(token))
        elif token.startswith('[jump:'):
            target = token[6:-1].strip()
            if target.isdigit() and 1 <= int(target) <= content.count('[newpage]') + 1:
                parts.append('<a href="#page-{}">第 {} 页</a>'.format(int(target), int(target)))
            else:
                parts.append(xml(token))
        end = match.end()
    parts.append(xml(content[end:]).replace('\n', '<br/>') + '</div>')
    title, author = xml(body['title']), xml(body['userName'])
    language = xml(body.get('language') or 'und')
    source = 'https://www.pixiv.net/novel/show.php?id=' + nid

    def xhtml(title, text):
        return ('<?xml version="1.0" encoding="utf-8"?>'
                '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"'
                ' xml:lang="{}"><head><title>{}</title><style>'
                'img{{max-width:100%;height:auto}} .page{{break-before:page}}'
                '.illustration{{text-align:center;margin:1em 0}}'
                '</style></head><body>{}</body></html>').format(language, title, text)

    book = xhtml(title, '<h1>{}</h1><p>{}</p><p><a href="{}">Pixiv</a></p>{}'.format(title, author, source, ''.join(parts)))
    toc = [('text.xhtml', body['title'])] + [('text.xhtml#' + anchor, name) for anchor, name in headings]
    nav = xhtml('目录', '<nav epub:type="toc" id="toc"><h1>目录</h1><ol>{}</ol></nav>'.format(''.join(
        '<li><a href="{}">{}</a></li>'.format(href, xml(name)) for href, name in toc)))
    manifest = ['<item id="text" href="text.xhtml" media-type="application/xhtml+xml"/>',
                '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>']
    for index, (name, mime, data) in enumerate(resources):
        manifest.append('<item id="image-{}" href="{}" media-type="{}"{}/>'.format(
            index, name, mime, ' properties="cover-image"' if name == cover else ''))
    spine = '<itemref idref="text"/>'
    if cover:
        manifest.append('<item id="cover" href="cover.xhtml" media-type="application/xhtml+xml"/>')
        spine = '<itemref idref="cover"/>' + spine
    try:
        modified = datetime.fromisoformat((body.get('uploadDate') or body['createDate']).replace('Z', '+00:00'))
        modified = modified.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    except (ValueError, KeyError, AttributeError):
        modified = '2000-01-01T00:00:00Z'
    opf = ('<?xml version="1.0" encoding="utf-8"?>'
           '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id">'
           '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
           '<dc:identifier id="book-id">{}</dc:identifier><dc:title>{}</dc:title>'
           '<dc:creator>{}</dc:creator><dc:language>{}</dc:language><meta property="dcterms:modified">{}</meta>'
           '</metadata><manifest>{}</manifest><spine>{}</spine></package>').format(
               source, title, author, language, modified, ''.join(manifest), spine)
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        def write(name, data, compression=zipfile.ZIP_DEFLATED):
            # 固定 ZIP 时间戳，使相同内容生成一致的文件。
            item = zipfile.ZipInfo(name, (2000, 1, 1, 0, 0, 0))
            item.compress_type = compression
            archive.writestr(item, data)
        write('mimetype', b'application/epub+zip', zipfile.ZIP_STORED)
        write('META-INF/container.xml', '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        write('OEBPS/content.opf', opf)
        write('OEBPS/text.xhtml', book)
        write('OEBPS/nav.xhtml', nav)
        if cover:
            write('OEBPS/cover.xhtml', xhtml('封面', '<div><img src="{}" alt="封面"/></div>'.format(cover)))
        for name, mime, data in resources:
            write('OEBPS/' + name, data)
    return output.getvalue()
