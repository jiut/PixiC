# coding=utf8
r"""
单独测试下载脚本：

用法示例：
1) 直链图片/ZIP：
   python test_download.py --url https://i.pximg.net/img-original/.../12345678_p0.jpg --out E:/tmp/12345678.jpg

2) 通过作品 PID：
   python test_download.py --pid 131979037  --outdir "C:\\Users\\jiut\\Downloads"

说明：
- 需要可用的 Pixiv 登录 cookie（与主程序一致）。
- PID 模式下：
  - 单图保存为 <pid>.<ext>
  - 多图保存为 <pid>/<pid>-<index>.<ext>
  - 动图仅测试 ZIP 下载并保存为 <pid>/<pid>.zip（不做 GIF 合成）

可选：
- 若需在测试前强制刷新 cookie（当 COOKIE_UPDATE_ENABLED=True 时会启动浏览器获取新 cookie），使用 --refresh-cookie
"""

import argparse
import json
import os
import sys

from downer import Downloader
from login import client as login_client
from log_record import logger


def ensure_dir(path: str) -> None:
    if not os.path.exists(path):
        os.makedirs(path, exist_ok=True)


def do_download(d: Downloader, url: str, dest_path: str):
    """使用现有 Downloader 的 API 下载文件到指定路径。返回字节大小或 None。"""
    ensure_dir(os.path.dirname(dest_path) or '.')
    r = d.baseRequest(options={"url": url})
    if r is None:
        return None
    try:
        return d.downSomething(dest_path, r.content)
    except Exception:
        return None


def filename_from_url(url: str) -> str:
    return url.split("?")[0].split("/")[-1]


def test_download_by_url(url: str, out_path: str) -> None:
    d = Downloader()
    # 自动推断文件名
    dest_path = out_path
    if not dest_path:
        dest_path = os.path.join(os.getcwd(), filename_from_url(url))
    ensure_dir(os.path.dirname(dest_path) or '.')
    logger.info(f"开始下载: {url} -> {dest_path}")
    size = do_download(d, url, dest_path)
    if size is None:
        logger.warning("下载失败")
        sys.exit(2)
    logger.success(f"下载完成: {dest_path} 大小: {d.size2Mb(size)}")


def test_download_by_pid(pid: int, outdir: str) -> None:
    d = Downloader()
    ensure_dir(outdir)

    info_url = d.ajax_illust.format(pid)
    r = d.baseRequest(options={"url": info_url, "headers": d.ajax_headers})
    if r is None:
        logger.warning("获取作品信息失败：网络异常")
        sys.exit(2)

    try:
        data = json.loads(r.text)
    except Exception:
        logger.warning("获取作品信息失败：JSON 解析错误")
        sys.exit(2)

    if data.get("error"):
        logger.warning(f"获取作品信息失败：{data.get('message')}")
        sys.exit(2)

    body = data.get("body", {})
    illust = body.get("illust_details", {})
    illust_type = int(illust.get("type", 0))
    page_count = int(illust.get("page_count", 1))

    pid_dir = os.path.join(outdir, str(pid))
    ensure_dir(pid_dir)

    if page_count == 1 and illust_type != 2:
        # 单图
        original = illust.get("url_big")
        if not original:
            logger.warning("未获取到原图链接")
            sys.exit(2)
        ext = original.split(".")[-1]
        dest = os.path.join(pid_dir, f"{pid}.{ext}")
        logger.info(f"开始下载单图: {original} -> {dest}")
        size = do_download(d, original, dest)
        if size is None:
            logger.warning("下载失败")
            sys.exit(2)
        logger.success(f"下载完成: {dest} 大小: {d.size2Mb(size)}")
        return

    if page_count > 1 and illust_type != 2:
        # 多图
        pages_url = d.multi_url.format(pid)
        m = d.baseRequest(options={"url": pages_url})
        if m is None:
            logger.warning("获取多图信息失败：网络异常")
            sys.exit(2)
        try:
            m_json = json.loads(m.text)
        except Exception:
            logger.warning("获取多图信息失败：JSON 解析错误")
            sys.exit(2)
        if m_json.get("error"):
            logger.warning("获取多图信息失败：接口错误")
            sys.exit(2)
        pages = m_json.get("body", [])
        if not pages:
            logger.warning("多图为空")
            sys.exit(2)
        for idx, p in enumerate(pages):
            url = p.get("urls", {}).get("original")
            if not url:
                logger.warning(f"第 {idx} 张缺少原图链接，跳过")
                continue
            name = filename_from_url(url).replace("_p", "-")
            dest = os.path.join(pid_dir, name)
            logger.info(f"开始下载第 {idx} 张: {url} -> {dest}")
            size = do_download(d, url, dest)
            if size is None:
                logger.warning(f"第 {idx} 张下载失败")
            else:
                logger.success(f"第 {idx} 张完成: {dest} 大小: {d.size2Mb(size)}")
        return

    # 动图（仅测试 ZIP 下载）
    meta_url = d.zip_url.format(pid)
    z = d.baseRequest(options={"url": meta_url})
    if z is None:
        logger.warning("获取动图 ZIP 信息失败：网络异常")
        sys.exit(2)
    try:
        z_json = json.loads(z.text)
    except Exception:
        logger.warning("获取动图 ZIP 信息失败：JSON 解析错误")
        sys.exit(2)
    if z_json.get("error"):
        logger.warning("获取动图 ZIP 信息失败：接口错误")
        sys.exit(2)
    zip_url = z_json.get("body", {}).get("originalSrc")
    if not zip_url:
        logger.warning("未获取到动图 ZIP 链接")
        sys.exit(2)
    dest = os.path.join(pid_dir, f"{pid}.zip")
    logger.info(f"开始下载动图 ZIP: {zip_url} -> {dest}")
    size = do_download(d, zip_url, dest)
    if size is None:
        logger.warning("ZIP 下载失败")
        sys.exit(2)
    logger.success(f"ZIP 下载完成: {dest} 大小: {d.size2Mb(size)}")


def main():
    parser = argparse.ArgumentParser(description="PixiC 单独测试下载")
    parser.add_argument("--url", help="直链 URL（图片或ZIP）", default=None)
    parser.add_argument("--out", help="直链保存文件路径（含文件名）", default=None)
    parser.add_argument("--pid", help="作品 PID（与 --url 二选一）", type=int, default=None)
    parser.add_argument("--outdir", help="PID 下载保存目录", default=os.path.join(os.getcwd(), "test_downloads"))
    parser.add_argument("--refresh-cookie", action="store_true", help="在下载前按配置刷新/更新 cookie")
    args = parser.parse_args()

    if not args.url and not args.pid:
        parser.error("必须提供 --url 或 --pid 其中之一")

    # 可选：刷新/更新 cookie
    if args["refresh_cookie"] if isinstance(args, dict) else getattr(args, "refresh_cookie", False):
        # 与 scheduler 一致，调用 login.client.check()
        login_client.check()

    if args.url:
        test_download_by_url(args.url, args.out)
    else:
        test_download_by_pid(args.pid, args.outdir)


if __name__ == "__main__":
    main()


