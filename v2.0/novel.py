# coding=utf8
"""收藏小说归档。接口参考 PixivBatchDownloader/src/ts/API.ts。"""
import json
import os
import time
import re
import zipfile
import posixpath
from urllib.parse import urlsplit
from urllib.parse import unquote
from xml.etree import ElementTree as ET
from pathlib import Path

from config import (BOOKMARK_HIDE_ENABLE, NOVEL_PATH, NOVEL_UPDATE_CHECK_LIMIT,
                    NOVEL_INTEGRITY_CHECK_LIMIT, SKIP_DB_EXISTS_NOVEL)
from log_record import logger
from message import TEMP_MSG
from novel_epub import build_epub


class NovelNotFoundError(RuntimeError):
    """小说详情明确返回 404，不应重试。"""


class Novel:
    def __init__(self, downloader=None, root=None):
        if downloader is None:
            from downer import Downloader
            downloader = Downloader()
        self.downloader = downloader
        self.db = downloader.db
        self.root = Path(root if root is not None else (NOVEL_PATH or 'novel')).resolve()
        self.rest_list = ['show', 'hide'] if BOOKMARK_HIDE_ENABLE else ['show']
        self.bookmark_url = 'https://www.pixiv.net/ajax/user/{}/novels/bookmarks'.format(
            downloader.client.user_id)
        self.page_size = 48

    def _bookmark_page(self, rest='show', limit=None):
        body = self.request(self.bookmark_url, {
            'tag': '', 'offset': 0, 'limit': limit or self.page_size, 'rest': rest})
        works = body.get('works')
        if not isinstance(works, list):
            raise ValueError('无效小说收藏列表')
        return works

    def check_update(self):
        """只比较最新收藏，返回 changed、unchanged 或 error。"""
        if not hasattr(self.db, 'pool'):
            logger.warning('Novel: 未启用数据库，执行全量更新')
            return 'changed'
        try:
            show = self._bookmark_page('show', NOVEL_UPDATE_CHECK_LIMIT)
        except Exception as exc:
            logger.warning('Novel: 检查公开收藏更新失败: {}', exc)
            return 'error'
        hide = []
        if BOOKMARK_HIDE_ENABLE:
            try:
                hide = self._bookmark_page('hide', NOVEL_UPDATE_CHECK_LIMIT)
            except Exception as exc:
                logger.warning('Novel: 检查私密收藏更新失败: {}', exc)
                return 'error'
        latest = show[:NOVEL_UPDATE_CHECK_LIMIT] + hide[:NOVEL_UPDATE_CHECK_LIMIT]
        if not latest:
            logger.info('Novel: 公开/私密收藏均为空，暂不更新')
            return 'unchanged'
        for work in latest:
            try:
                nid = int(work['id'])
            except (KeyError, TypeError, ValueError):
                continue
            try:
                exists, _ = self.db.check_illust(nid, key='nid', table='novel')
            except Exception as exc:
                logger.warning('Novel: 检查作品 {} 失败: {}', nid, exc)
                return 'error'
            try:
                unavailable = bool(getattr(self.db, 'is_novel_unavailable', lambda _: False)(nid))
            except Exception as exc:
                logger.warning('Novel: 检查作品 {} 不可访问状态失败: {}', nid, exc)
                return 'error'
            if not exists and not unavailable:
                logger.info('Novel: 检测到新收藏，开始更新')
                return 'changed'
        logger.info('Novel: 收藏未变化，暂不更新')
        return 'unchanged'

    def integrity_check(self):
        """只检查数据库最近20部 EPUB，输出汇总，不逐条输出通过项。"""
        if not hasattr(self.db, 'pool'):
            return {'total': 0, 'passed': 0, 'repaired': 0, 'failed': 0}
        try:
            records = self.db.select_latest_novels(NOVEL_INTEGRITY_CHECK_LIMIT)
        except Exception as exc:
            logger.warning('Novel: 一致性检查查询失败: {}', exc)
            return {'total': 0, 'passed': 0, 'repaired': 0, 'failed': 0,
                    'query_failed': 1, 'deleted': 0}
        stats = {'total': len(records), 'passed': 0, 'repaired': 0, 'failed': 0,
                 'query_failed': 0, 'deleted': 0}
        for record in records:
            path = record.get('path')
            ok = bool(path and os.path.isfile(path))
            if ok:
                try:
                    ok = self.is_valid_epub(path)
                except (OSError, KeyError, UnicodeError, zipfile.BadZipFile):
                    ok = False
            if ok:
                stats['passed'] += 1
                continue
            try:
                if bool(getattr(self.db, 'is_novel_unavailable', lambda _: False)(int(record['nid']))):
                    stats['deleted'] += 1
                    continue
                self.save_novel(int(record['nid']), emit_success=False)
                stats['repaired'] += 1
            except NovelNotFoundError:
                nid = int(record['nid'])
                self._mark_unavailable(nid, TEMP_MSG['PID_DELETED_TEXT'])
                stats['deleted'] += 1
                logger.warning('{} - {}', TEMP_MSG['PID_DELETED_TEXT'], nid)
            except Exception as exc:
                stats['failed'] += 1
                logger.warning('Novel: 一致性修复失败 nid={}: {}', record.get('nid'), exc)
        logger.info('Novel: 一致性检查完成 | 最近 {} 部 总数:{} 通过:{} 修复:{} 失败:{} 404:{}',
                    NOVEL_INTEGRITY_CHECK_LIMIT, stats['total'], stats['passed'],
                    stats['repaired'], stats['failed'], stats['deleted'])
        return stats

    def _mark_unavailable(self, nid, reason):
        marker = getattr(self.db, 'mark_novel_unavailable', None)
        if hasattr(self.db, 'pool') and callable(marker):
            marker(int(nid), reason)

    @staticmethod
    def is_valid_epub(path):
        """验证 EPUB 必需文件以及 XHTML 中引用的本地资源。"""
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            if archive.testzip() is not None:
                return False
            if 'mimetype' not in names or archive.read('mimetype') != b'application/epub+zip':
                return False
            required = {'META-INF/container.xml', 'OEBPS/content.opf',
                        'OEBPS/text.xhtml', 'OEBPS/nav.xhtml'}
            if not required.issubset(names):
                return False
            try:
                documents = {name: ET.fromstring(archive.read(name)) for name in names
                             if name.endswith(('.xml', '.opf', '.xhtml'))}
                ns = {'opf': 'http://www.idpf.org/2007/opf'}
                package = documents['OEBPS/content.opf']
                items = package.findall('opf:manifest/opf:item', ns)
                manifest = {item.attrib['id']: item for item in items}
                if not manifest or len(manifest) != len(items):
                    return False
                spine = package.findall('opf:spine/opf:itemref', ns)
                if not spine or any(item.get('idref') not in manifest for item in spine):
                    return False
                if not any('nav' in item.get('properties', '').split() for item in items):
                    return False
                for item in items:
                    target = posixpath.normpath(posixpath.join('OEBPS', unquote(item.attrib['href'])))
                    if target not in names:
                        return False
                    if item.get('media-type', '').startswith('image/'):
                        from novel_epub import image_data
                        image_data(archive.read(target))
                for name, document in documents.items():
                    if not name.endswith('.xhtml'):
                        continue
                    for element in document.iter():
                        for attribute in ('src', 'href'):
                            ref = element.get(attribute)
                            if ref is None:
                                continue
                            url = urlsplit(ref)
                            if url.scheme or url.netloc:
                                continue
                            target = (posixpath.normpath(posixpath.join(posixpath.dirname(name), unquote(url.path)))
                                      if url.path else name)
                            if target not in names:
                                return False
                            if url.fragment and target in documents:
                                if not any(e.get('id') == unquote(url.fragment)
                                           for e in documents[target].iter()):
                                    return False
            except (ET.ParseError, KeyError, ValueError, OSError):
                return False
            return True

    def request(self, url, params=None):
        # 有限重试：错误响应不能当成空收藏，也不能无限循环。
        for attempt in range(3):
            try:
                response = self.downloader.baseRequest({'url': url}, params=params, retry_num=0)
                if response is None:
                    raise ValueError('请求失败')
                if (getattr(response, 'status_code', None) == 404
                        and re.fullmatch(r'/ajax/novel/\d+/?', urlsplit(url).path)):
                    raise NovelNotFoundError(TEMP_MSG['PID_DELETED_TEXT'])
                payload = json.loads(response.text)
                if payload.get('error') or not isinstance(payload.get('body'), dict):
                    raise ValueError(payload.get('message') or '无效响应')
                return payload['body']
            except (ValueError, TypeError, AttributeError) as exc:
                if attempt == 2:
                    raise RuntimeError('小说接口请求失败（3 次尝试）: {} - {}'.format(url, exc)) from exc
                logger.warning('NOVEL_REQUEST_RETRY | 响应/网络错误 | 小说接口重试 {}/3，{} 秒后重试: {} - {}', attempt + 1, 2 ** attempt, url, exc)
                time.sleep(2 ** attempt)

    def save_novel(self, nid, emit_success=True):
        nid = int(nid)
        if nid <= 0:
            raise ValueError('无效小说 ID')
        body = self.request('https://www.pixiv.net/ajax/novel/{}'.format(nid))
        if str(body.get('id')) != str(nid) or not isinstance(body.get('content'), str):
            raise ValueError('小说正文缺失或 ID 不匹配')
        tags = [tag['tag'] for tag in body.get('tags', {}).get('tags', [])]
        directory = self.root / str(nid)
        path = directory / '{}.epub'.format(nid)
        info = {
            'nid': nid, 'uid': int(body['userId']), 'userName': body['userName'],
            'purl': 'https://www.pixiv.net/novel/show.php?id={}'.format(nid),
            'title': body['title'], 'description': body.get('description') or '',
            'tag': '、'.join(tags), 'createDate': body.get('createDate') or '',
            'updateDate': body.get('uploadDate') or '',
            'viewCount': int(body.get('viewCount') or 0),
            'bookmarkCount': int(body.get('bookmarkCount') or 0),
            'is_r18': int(int(body.get('xRestrict') or 0) > 0), 'path': str(path),
        }
        # 保留 Pixiv 原始正文标记，避免注音、插图引用和章节信息丢失。
        text = '{}\n作者：{}\n{}\n\n{}'.format(
            info['title'], info['userName'], info['purl'], body['content'])
        directory.mkdir(parents=True, exist_ok=True)
        if emit_success:
            logger.info('小说 {}: 开始生成 EPUB', nid)
        def progress(current, total, url):
            if not emit_success:
                return
            if current == 1 or current % 10 == 0 or current == total:
                logger.info('小说 {}: 图片处理 {}/{}', nid, current, total)
            logger.debug('小说 {}: 图片 {}/{} - {}', nid, current, total, url)
        epub = build_epub(body, self.request, self.download_image, progress=progress)
        changed = self.atomic_write(path, epub)
        self.atomic_write(directory / '{}.txt'.format(nid), text)
        self.atomic_write(directory / 'metadata.json', json.dumps(body, ensure_ascii=False, indent=2))
        if hasattr(self.db, 'pool'):
            self.db.upsert_novel(info)
        if changed and emit_success:
            logger.success('小说 {}: EPUB 已保存，{} 字节；数据库{}', nid, len(epub),
                           '已更新' if hasattr(self.db, 'pool') else '未启用')
        elif emit_success:
            logger.info('小说 {}: EPUB 内容未变，校验完成；数据库{}', nid,
                        '已更新' if hasattr(self.db, 'pool') else '未启用')
        return info

    def download_image(self, url):
        from novel_epub import image_data
        for attempt in range(3):
            try:
                response = self.downloader.baseRequest({'url': url}, retry_num=0)
                if response is None:
                    raise ValueError('图片请求失败')
                image_data(response.content)
                return response.content
            except (ValueError, OSError, AttributeError) as exc:
                if attempt == 2:
                    raise RuntimeError('小说图片下载失败（3 次尝试）: {} - {}'.format(url, exc)) from exc
                logger.warning('NOVEL_IMAGE_RETRY | 图片响应/格式错误 | 小说图片重试 {}/3，{} 秒后重试: {} - {}', attempt + 1, 2 ** attempt, url, exc)
                time.sleep(2 ** attempt)

    @staticmethod
    def atomic_write(path, text):
        content = text if isinstance(text, bytes) else text.encode('utf-8')
        if path.is_file() and path.read_bytes() == content:
            return False
        temporary = path.with_suffix(path.suffix + '.tmp')
        try:
            with temporary.open('wb') as stream:
                stream.write(content)
            os.replace(str(temporary), str(path))
        finally:
            if temporary.exists():
                temporary.unlink()
        return True

    def run(self):
        stats = {'success': 0, 'failed': 0, 'page_failed': 0, 'deleted': 0,
                 'skipped_existing': 0, 'aborted': False, 'skipped': False,
                 'check_failed': 0, 'integrity_failed': 0,
                 'integrity_query_failed': 0, 'integrity_deleted': 0}
        logger.info(TEMP_MSG['BEGIN_INFO'].format('Novel'))
        try:
            self._run(stats)
        except Exception:
            stats['aborted'] = True
            logger.opt(exception=True).error('Novel: 本轮异常中断，等待下次轮询')
        incomplete = (stats['aborted'] or stats['failed'] or stats['page_failed']
                      or stats.get('check_failed', 0)
                      or stats.get('integrity_failed', 0)
                      or stats.get('integrity_query_failed', 0))
        log = logger.warning if incomplete else logger.info
        status = '未完整完成' if incomplete else ('跳过（收藏未变化）' if stats['skipped'] else '完成')
        log('NOVEL_ROUND_SUMMARY | 小说收藏本轮{}：处理成功 {} 部，跳过已有 {} 部，作品失败 {} 次，分页失败 {} 次，更新检查失败 {} 次，一致性失败 {} 次，404 跳过 {} 部',
            status, stats['success'], stats['skipped_existing'], stats['failed'],
            stats['page_failed'], stats.get('check_failed', 0),
            stats.get('integrity_failed', 0) + stats.get('integrity_query_failed', 0),
            stats['deleted'] + stats.get('integrity_deleted', 0))
        return stats

    def _run(self, stats):
        if hasattr(self.db, 'pool'):
            try:
                self.db.ensure_novel_table()
                ensure_unavailable = getattr(self.db, 'ensure_novel_unavailable_table', None)
                if callable(ensure_unavailable):
                    ensure_unavailable()
            except Exception as exc:
                stats['aborted'] = True
                logger.opt(exception=True).error('小说表初始化失败: {}', exc)
                return
        integrity = self.integrity_check()
        stats['integrity_failed'] = integrity.get('failed', 0)
        stats['integrity_query_failed'] = integrity.get('query_failed', 0)
        stats['integrity_deleted'] = integrity.get('deleted', 0)
        update_state = self.check_update()
        if update_state in (None, 'error'):
            stats['check_failed'] += 1
            logger.warning('Novel: 更新检查失败，本轮不执行详情抓取，等待下次轮询')
            return
        if update_state in (False, 'unchanged'):
            stats['skipped'] = True
            logger.info('Novel: 本轮收藏未变化，跳过详情抓取')
            return
        seen = set()
        for rest in self.rest_list:
            label = {'show': '公开', 'hide': '私密'}.get(rest, rest)
            logger.info('Novel: 开始获取{}收藏', label)
            offset = 0
            while True:
                try:
                    page = self.request(self.bookmark_url, {
                        'tag': '', 'offset': offset, 'limit': self.page_size, 'rest': rest})
                    works = page['works']
                    if not isinstance(works, list):
                        raise ValueError('无效收藏列表')
                    total = int(page['total']) if 'total' in page else None
                    if total is not None and total < 0:
                        raise ValueError('无效收藏总数')
                except Exception as exc:
                    stats['page_failed'] += 1
                    logger.warning('NOVEL_PAGE_FAILED | 小说收藏分页失败（{} / {}）: {}'.format(rest, offset, exc))
                    break
                logger.info('Novel: {}收藏 offset={}，本页 {} 部，总数 {}', label, offset, len(works),
                            total if total is not None else '未知')
                if not works:
                    break
                for work in works:
                    work_id = work.get('id', '未知') if isinstance(work, dict) else '无效条目'
                    try:
                        nid = int(work_id)
                        if nid <= 0:
                            raise ValueError('无效小说 ID')
                        if nid in seen:
                            continue
                        if hasattr(self.db, 'pool') and bool(
                                getattr(self.db, 'is_novel_unavailable', lambda _: False)(nid)):
                            seen.add(nid)
                            stats['deleted'] += 1
                            logger.debug('SKIP_UNAVAILABLE_NOVEL - {}', nid)
                            continue
                        if hasattr(self.db, 'pool') and SKIP_DB_EXISTS_NOVEL:
                            try:
                                exists, _ = self.db.check_illust(nid, key='nid', table='novel')
                                if exists:
                                    seen.add(nid)
                                    stats['skipped_existing'] += 1
                                    logger.info('SKIP_DB_EXISTS_NOVEL - {}', nid)
                                    continue
                            except Exception as exc:
                                logger.debug('Novel: 检查已有记录失败，继续抓取 {}: {}', nid, exc)
                        self.save_novel(nid)
                        seen.add(nid)
                        stats['success'] += 1
                    except NovelNotFoundError:
                        seen.add(nid)
                        self._mark_unavailable(nid, TEMP_MSG['PID_DELETED_TEXT'])
                        stats['deleted'] += 1
                        logger.warning('{} - {}', TEMP_MSG['PID_DELETED_TEXT'], nid)
                    except Exception as exc:
                        stats['failed'] += 1
                        logger.warning('NOVEL_DETAIL_FAILED | 小说抓取失败（{}）: {}', work_id, exc)
                offset += self.page_size
                if total is not None and offset >= total:
                    break


if __name__ == '__main__':
    from login import client
    client.check()
    Novel().run()
