# coding=utf8
"""
批量重下指定 PID 的文件：
- 仅写入文件，不修改数据库记录
- 从数据库查询已有记录路径（path）作为保存目录；若无 path，则按现有规则推导保存目录

用法示例：
  # 依据 missing.txt 全量重下
  python redownload_missing.py --file ..\\missing.txt --table pixiv
  
  # 从某个 PID 之后继续（跳过该 PID 及其之前的所有 PID）
  python redownload_missing.py --file ..\\missing.txt --table bookmark --from-pid 123190650

  # 只重下指定的若干 PID（忽略 --file）
  python redownload_missing.py --pids 123229998,123217985 --table bookmark

参数说明：
- --file: 含 PID 的文本文件（一行一个 PID）。提供 --pids 时可省略
- --pids: 逗号分隔的 PID 列表，仅重下这些 PID
- --from-pid: 从该 PID 之后开始（需要配合 --file）
- --table: 数据表名，pixiv 或 bookmark（影响从 DB 读取 path 及回退路径推导）

注意：
- 需要可用的 Pixiv 登录 cookie（与主程序一致）。
- 不会对数据库进行 insert/update/delete，只下载文件到目标目录。
"""

import argparse
import json
import os
import sys
from typing import Optional

from downer import Downloader
from folder import file_manager
from log_record import logger


def read_pid_file(path: str):
    if not os.path.exists(path):
        logger.warning(f"PID 文件不存在: {path}")
        sys.exit(2)
    pids = []
    with open(path, "r", encoding="utf8") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            if not s.isdigit():
                logger.warning(f"跳过非数字 PID: {s}")
                continue
            pids.append(int(s))
    return pids


def parse_pids_csv(pids_csv: str):
    targets = []
    for tok in (pids_csv or "").split(","):
        s = tok.strip()
        if not s:
            continue
        if not s.isdigit():
            logger.warning(f"跳过非法 PID: {s}")
            continue
        targets.append(int(s))
    return targets


def ensure_dir(path: str) -> None:
    if not os.path.exists(path):
        os.makedirs(path, exist_ok=True)


def do_download(d: Downloader, url: str, dest_path: str) -> Optional[int]:
    """使用 Downloader 的会话与头下载到指定路径，返回大小（字节）或 None。"""
    ensure_dir(os.path.dirname(dest_path) or ".")
    r = d.baseRequest(options={"url": url})
    if r is None:
        return None
    try:
        return d.downSomething(dest_path, r.content)
    except Exception:
        return None


def redownload_one(d: Downloader, pid: int, table: str) -> None:
    # 先尝试从 DB 获取已保存的路径
    save_path = None
    try:
        exists, path = d.db.check_illust(pid, key="pid", table=table)
        if exists and path and path != "None":
            save_path = path
    except Exception:
        save_path = None

    # 获取作品信息（不触碰 DB）
    info_url = d.ajax_illust.format(pid)
    r = d.baseRequest(options={"url": info_url, "headers": d.ajax_headers})
    if r is None:
        logger.warning(f"[{pid}] 获取作品信息失败：网络异常")
        return
    try:
        data = json.loads(r.text)
    except Exception:
        logger.warning(f"[{pid}] 获取作品信息失败：JSON 解析错误")
        return

    if data.get("error"):
        logger.warning(f"[{pid}] 获取作品信息失败：{data.get('message')}")
        return

    body = data.get("body", {})
    illust = body.get("illust_details", {})
    illust_type = int(illust.get("type", 0))
    page_count = int(illust.get("page_count", 1))

    # 若 DB 无 path，推导保存目录（不写回 DB）
    if not save_path:
        if table == "bookmark":
            user_dir = file_manager.bk_path
        else:
            uid = int(body.get("author_details", {}).get("user_id", 0))
            user_name = body.get("author_details", {}).get("user_name", "")
            user_dir = file_manager.select_user_path(uid, user_name)
        save_path = file_manager.mkdir_illusts(user_dir, pid)

    # 开始下载
    if page_count == 1 and illust_type != 2:
        # 单图
        original = illust.get("url_big")
        if not original:
            logger.warning(f"[{pid}] 未获取到原图链接")
            return
        ext = original.split(".")[-1]
        dest = os.path.join(save_path, f"{pid}.{ext}")
        logger.info(f"[{pid}] 下载单图: {original} -> {dest}")
        size = do_download(d, original, dest)
        if size is None:
            logger.warning(f"[{pid}] 下载失败")
        else:
            logger.success(f"[{pid}] 完成: {dest} 大小: {d.size2Mb(size)}")
        return

    if page_count > 1 and illust_type != 2:
        # 多图
        pages_url = d.multi_url.format(pid)
        m = d.baseRequest(options={"url": pages_url})
        if m is None:
            logger.warning(f"[{pid}] 获取多图信息失败：网络异常")
            return
        try:
            m_json = json.loads(m.text)
        except Exception:
            logger.warning(f"[{pid}] 获取多图信息失败：JSON 解析错误")
            return
        if m_json.get("error"):
            logger.warning(f"[{pid}] 获取多图信息失败：接口错误")
            return
        pages = m_json.get("body", [])
        if not pages:
            logger.warning(f"[{pid}] 多图为空")
            return
        for idx, p in enumerate(pages):
            url = p.get("urls", {}).get("original")
            if not url:
                logger.warning(f"[{pid}] 第 {idx} 张缺少原图链接，跳过")
                continue
            name = url.split("?")[0].split("/")[-1].replace("_p", "-")
            dest = os.path.join(save_path, name)
            logger.info(f"[{pid}] 下载第 {idx} 张: {url} -> {dest}")
            size = do_download(d, url, dest)
            if size is None:
                logger.warning(f"[{pid}] 第 {idx} 张下载失败")
            else:
                logger.success(f"[{pid}] 第 {idx} 张完成: {dest} 大小: {d.size2Mb(size)}")
        return

    # 动图（仅 ZIP）
    meta_url = d.zip_url.format(pid)
    z = d.baseRequest(options={"url": meta_url})
    if z is None:
        logger.warning(f"[{pid}] 获取动图 ZIP 信息失败：网络异常")
        return
    try:
        z_json = json.loads(z.text)
    except Exception:
        logger.warning(f"[{pid}] 获取动图 ZIP 信息失败：JSON 解析错误")
        return
    if z_json.get("error"):
        logger.warning(f"[{pid}] 获取动图 ZIP 信息失败：接口错误")
        return
    zip_url = z_json.get("body", {}).get("originalSrc")
    if not zip_url:
        logger.warning(f"[{pid}] 未获取到动图 ZIP 链接")
        return
    dest = os.path.join(save_path, f"{pid}.zip")
    logger.info(f"[{pid}] 下载动图 ZIP: {zip_url} -> {dest}")
    size = do_download(d, zip_url, dest)
    if size is None:
        logger.warning(f"[{pid}] ZIP 下载失败")
        return
    logger.success(f"[{pid}] ZIP 完成: {dest} 大小: {d.size2Mb(size)}")


def main():
    parser = argparse.ArgumentParser(description="批量重下指定 PID 的文件（不改数据库）")
    parser.add_argument("--file", help="包含 PID 的文本文件路径（每行一个 PID）")
    parser.add_argument("--pids", help="逗号分隔的 PID 列表，仅重下这些 PID，如: 123,456", default=None)
    parser.add_argument("--from-pid", type=int, help="从该 PID 之后开始（需要配合 --file）", default=None)
    parser.add_argument("--table", default="pixiv", choices=["pixiv", "bookmark"], help="数据库数据表")
    args = parser.parse_args()

    # 计算待处理 PID 列表
    pids = []
    if args.pids:
        pids = parse_pids_csv(args.pids)
        if not pids:
            logger.warning("--pids 为空或非法")
            sys.exit(2)
    else:
        if not args.file:
            logger.warning("未提供 --file 或 --pids")
            sys.exit(2)
        all_pids = read_pid_file(args.file)
        if args.from_pid is not None:
            try:
                idx = all_pids.index(args.from_pid)
                pids = all_pids[idx + 1 :]
            except ValueError:
                logger.warning(f"在文件中未找到 --from-pid: {args.from_pid}，将使用全量列表")
                pids = all_pids
        else:
            pids = all_pids

    if not pids:
        logger.warning("PID 列表为空")
        sys.exit(2)

    d = Downloader()

    for pid in pids:
        try:
            redownload_one(d, pid, args.table)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            logger.warning(f"[{pid}] 异常: {e}")


if __name__ == "__main__":
    main()


