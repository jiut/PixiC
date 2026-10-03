# -*- encoding: utf-8 -*-
'''
@File    :   log_record.py
@Time    :   2020/04/04
@Author  :   Coder-Sakura
@Version :   1.0
@Desc    :   log
'''

# here put the import lib
import os
import sys
from loguru import logger
from config import DEBUG
import threading
import re
import atexit
import datetime
# 终端模式下各进程都启用跳过日志聚合；重定向到文件/管道时保留完整日志行。
# 进程间的聚合计数彼此独立，但仍可避免同一抓取进程刷屏。
_dynamic_console = sys.stderr.isatty()

if DEBUG:
    level = "DEBUG"
else:
    level = "INFO"

log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "log")

# remove default handler
logger.remove()

# ============== 控制台单行汇聚日志（多前缀） ==============
_agg_state = {
    "active": False,      # 是否处于单行显示状态
    "width": 0,           # 当前行宽，用于清除
    "count": 0,           # 当前类别累计计数
    "category": None,     # 当前类别标识
}
_agg_lock = threading.Lock()
_context_lock = threading.Lock()
_log_context = {"batch_id": None, "phase": "preamble", "errors": []}
_ansi_re = re.compile(r"\x1b\[[0-9;]*m")


def set_log_context(batch_id=None, phase="preamble"):
    with _context_lock:
        # 每个阶段独立统计 sink 错误，避免上一阶段的故障污染下一阶段。
        if batch_id != _log_context.get("batch_id") or phase != _log_context.get("phase"):
            _log_context["errors"] = []
        _log_context["batch_id"] = batch_id
        _log_context["phase"] = phase


def get_sink_errors(clear=False):
    with _context_lock:
        errors = list(_log_context["errors"])
        if clear:
            _log_context["errors"] = []
        return errors


def _record_sink_error(name, exc):
    with _context_lock:
        _log_context["errors"].append({
            "sink": name,
            "phase": _log_context["phase"],
            "batch_id": _log_context["batch_id"],
            "error": repr(exc),
        })


def complete_phase():
    # 先结束动态聚合行，确保阶段摘要不会与残留的进度行粘连。
    try:
        with _agg_lock:
            _clear_agg_line()
    except Exception as exc:
        _record_sink_error("console-finalize", exc)
    complete = getattr(logger, "complete", None)
    if complete:
        try:
            complete()
        except Exception as exc:
            _record_sink_error("logger-complete", exc)
    # 读取后清零；下一次阶段只报告自己的写入错误。
    return get_sink_errors(clear=True)


def _patch_record(record):
    """将当前批次/阶段上下文复制到每条 Loguru 记录。"""
    with _context_lock:
        record.setdefault("extra", {})["batch_id"] = _log_context.get("batch_id")
        record.setdefault("extra", {})["phase"] = _log_context.get("phase")

def _visible_len(text: str) -> int:
    try:
        return len(_ansi_re.sub("", text))
    except Exception:
        return len(text)
def _erase_line():
    """清除整行内容并将光标回到行首。"""
    try:
        # ANSI: 清除整行并回到行首，兼容支持虚拟终端的 Windows 终端/PowerShell 7+
        sys.stderr.write("\r\x1b[2K\r")
        # 兼容降级：若终端不支持 ANSI，则用空格覆盖当前记录宽度
        if _agg_state.get("width", 0) > 0:
            sys.stderr.write(" " * _agg_state["width"]) 
            sys.stderr.write("\r")
        sys.stderr.flush()
    except Exception:
        _record_sink_error("console-erase", sys.exc_info()[1])


def _is_skip_message(record):
    try:
        msg = record.get("message", "")
        return isinstance(msg, str) and msg.startswith("SKIP_ISEXISTS_ILLUST")
    except Exception:
        return False


def _is_bookmark_now_message(record):
    try:
        msg = record.get("message", "")
        return isinstance(msg, str) and msg.startswith("Pixiv收藏作品第")
    except Exception:
        return False


def _is_skip_db_message(record):
    try:
        msg = record.get("message", "")
        return isinstance(msg, str) and msg.startswith("SKIP_DB_EXISTS_ILLUST")
    except Exception:
        return False


def _is_skip_novel_message(record):
    try:
        msg = record.get("message", "")
        return isinstance(msg, str) and msg.startswith("SKIP_DB_EXISTS_NOVEL")
    except Exception:
        return False


def _clear_agg_line():
    if _agg_state["active"]:
        _erase_line()
        _agg_state["active"] = False
        _agg_state["width"] = 0
        _agg_state["count"] = 0
        _agg_state["category"] = None


def _general_console_sink(message: str):
    # 在输出其他日志前，若存在单行聚合状态，则先清除该行
    with _agg_lock:
        try:
            _clear_agg_line()
            sys.stderr.write(message)
            sys.stderr.flush()
        except Exception as exc:
            _record_sink_error("console", exc)


def _agg_write(category: str, message: str):
    # 单行动态刷新聚合输出
    with _agg_lock:
        # 切换类别时清空行并重置计数
        if _agg_state["category"] != category:
            _clear_agg_line()
            _agg_state["category"] = category
        _agg_state["count"] += 1
        text = f"{message.strip()} (x{_agg_state['count']})"
        try:
            # 使用 ANSI 清行，避免多字节/颜色转义导致的残留
            _erase_line()
            sys.stderr.write("\r" + text)
            sys.stderr.flush()
            _agg_state["active"] = True
            _agg_state["width"] = _visible_len(text)
        except Exception as exc:
            _record_sink_error("console-aggregate", exc)
            try:
                sys.stderr.write(message + "\n")
            except Exception:
                pass


def _skip_console_sink(message: str):
    _agg_write("skip_exists", message)


def _bookmark_now_console_sink(message: str):
    _agg_write("bookmark_now", message)


# 控制台输出（常规日志，不包括单行聚合的行）
logger.add(
    _general_console_sink,
    level=level,
    filter=lambda record: not _dynamic_console or not (
        _is_skip_message(record) or _is_bookmark_now_message(record) or
        _is_skip_db_message(record) or _is_skip_novel_message(record)
    ),
    colorize=True
)

# 控制台输出（仅处理以 SKIP_ISEXISTS_ILLUST 开头的行，单行动态刷新）
logger.add(
    _skip_console_sink,
    level=level,
    filter=lambda record: _dynamic_console and _is_skip_message(record),
    format="<level>{message}</level>",
    colorize=True
)

# 控制台输出（仅处理 BOOKMARK_NOW_INFO 行，单行动态刷新）
logger.add(
    _bookmark_now_console_sink,
    level=level,
    filter=lambda record: _dynamic_console and _is_bookmark_now_message(record),
    format="<level>{message}</level>",
    colorize=True
)

# 控制台输出（仅处理 SKIP_DB_EXISTS_ILLUST 行，单行动态刷新）
def _skip_db_console_sink(message: str):
    _agg_write("skip_db_exists", message)


logger.add(
    _skip_db_console_sink,
    level=level,
    filter=lambda record: _dynamic_console and _is_skip_db_message(record),
    format="<level>{message}</level>",
    colorize=True
)


def _skip_novel_console_sink(message: str):
    _agg_write("skip_db_exists_novel", message)


logger.add(
    _skip_novel_console_sink,
    level=level,
    filter=lambda record: _dynamic_console and _is_skip_novel_message(record),
    format="<level>{message}</level>",
    colorize=True
)

# patch 必须在所有 sink 注册后安装，保证控制台和异步文件记录携带相同上下文。
logger = logger.patch(_patch_record)

# 程序结束时，确保清除聚合行，避免残留
def _agg_finalize():
    try:
        with _agg_lock:
            _clear_agg_line()
    except Exception:
        _record_sink_error("console-finalize", sys.exc_info()[1])

atexit.register(_agg_finalize)

class _FileSink:
    """可观测的异步文件 sink，避免内建路径 sink 吞掉写入/轮转异常。"""
    def __init__(self, root):
        self.root = root
        self._lock = threading.RLock()
        self._day = None
        self._stream = None

    def _ensure_stream(self):
        day = datetime.datetime.now().strftime("%Y-%m-%d")
        if self._stream is not None and self._day == day:
            return
        if self._stream is not None:
            self._stream.close()
        os.makedirs(self.root, exist_ok=True)
        self._stream = open(os.path.join(self.root, day + ".log"), "a", encoding="utf-8")
        self._day = day

    def write(self, message):
        try:
            with self._lock:
                self._ensure_stream()
                self._stream.write(str(message))
                self._stream.flush()
        except Exception as exc:
            _record_sink_error("file", exc)

    def close(self):
        with self._lock:
            if self._stream is not None:
                try:
                    self._stream.close()
                finally:
                    self._stream = None


_file_sink = _FileSink(log_path)
logger.add(_file_sink.write, enqueue=True, level=level)
atexit.register(_file_sink.close)


# def log_str(*args, end=None):
#     for i in args:
#         print('[{}] {}'.format(time.strftime("%Y-%m-%d %H:%M:%S"),i),end=end)
