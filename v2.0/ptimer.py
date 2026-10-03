# coding=utf8
"""
通过智能等待处理限制访问
time: 2023-03-16
author: coder_sakura
"""

import time
import random
import threading

from message import TEMP_MSG
from log_record import logger


def random_cycle_time():
    return 20
    # return random.choice([30,40,50])


class Timer:
    """
    触发限制后周期性主动轮询
    或超过_limit_time后主动退出,进而恢复原先的任务
    """
    def __init__(self):
        # 每轮等待时间
        self.poll_cycle_time = random_cycle_time
        # 记录已等待时间
        self._time = 0
        # 官方限制时间-粗略估算
        self._limit_time = 180
        self._limit_time = 120
        self.Downloader = None
        self.pid = None
        self.extra = None

    def waiting(self, resp: str or dict, stop_event=None, deadline=None):
        """
        return: 
            resp: pid info
            func: 继续周期性轮询
        """
        while True:
            if stop_event is not None and stop_event.is_set():
                return None
            if deadline is not None and time.monotonic() >= deadline:
                return TEMP_MSG["LIMIT_TEXT"]
            if isinstance(resp, dict) and not resp.get("error"):
                logger.success(f"waiting:resp - {resp}")
                return resp
            if isinstance(resp, str) and resp != TEMP_MSG["LIMIT_TEXT"]:
                return resp
            wait = self.poll_cycle_time()
            if self._time + wait > self._limit_time:
                logger.warning("线程ID:{}:限流等待达到上限 {} 秒".format(
                    threading.get_ident(), self._limit_time))
                return TEMP_MSG["LIMIT_TEXT"]
            if deadline is not None:
                wait = min(wait, max(0, deadline - time.monotonic()))
            if stop_event is not None:
                if stop_event.wait(wait):
                    return None
            else:
                time.sleep(wait)
            self._time += wait
            logger.debug(f"线程ID:{threading.get_ident()}:已休眠{self._time}/{self._limit_time}秒,尝试重新访问<{self.pid}>")
            resp = self.Downloader.get_illust_info(self.pid, extra=self.extra)
