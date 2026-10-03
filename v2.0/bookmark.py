# coding=utf8
"""
用户收藏爬虫
time: 2020-05-11
author: coder_sakura
"""
# import math
import json
import time
import random
import threading

from config import BOOKMARK_HIDE_ENABLE,SKIP_ISEXISTS_ILLUST,SKIP_DB_EXISTS_ILLUST,BOOKMARK_PATH
from downer import Downloader
from log_record import logger
from message import TEMP_MSG
from thread_pool import ThreadPool,callback
from ptimer import Timer
# TODO
from tag import TAG_FLAG_BOOKMARK
from checker import run_startup_check
from phase_result import PhaseResult


class Bookmark(object):
	def __init__(self):
		self.Downloader = Downloader()
		self.user_id = self.Downloader.client.user_id
		self.base_request = self.Downloader.baseRequest
		
		self.bookmark_url = "https://www.pixiv.net/ajax/user/{}/illusts/bookmarks".format(self.user_id)
		self.rest_list = ["show", "hide"] if BOOKMARK_HIDE_ENABLE else ["show"]
		self.rest_dict = {"show": "公开", "hide": "未公开"}
		
		self.db = self.Downloader.db
		self.file_manager = self.Downloader.file_manager
		self.bookmark_page_offset = 48
		self.class_name = self.__class__.__name__

		# 2020/10/20 收藏更新机制
		# 默认第一次全更新
		self.day_count = 5
		self.day_all_update_num = 5
		self.day_limit = 800

	def _count_run_stat(self, name):
		stats = getattr(self, '_run_stats', None)
		if stats is None:
			return
		with self._run_stats_lock:
			stats[name] = stats.get(name, 0) + 1

	def _log_run_summary(self):
		stats = self._run_stats
		logger.info("{}:本轮汇总，成功 {} 部，跳过已有 {} 部，跳过文件 {} 部，失败 {} 次，404/不可访问 {} 部，分页失败 {} 次{}",
			self.class_name, stats["success"], stats["skipped_existing"],
			stats["skipped_file"], stats["failed"], stats["deleted"],
			stats["page_failed"], "，发生异常" if stats["aborted"] else "")

	def _task_callback(self, status, _result):
		"""线程池任务异常的统一统计回调。"""
		if not status:
			self._count_run_stat('failed')

	def get_page_bookmark(self, offset, rest="show", raw=False):
		"""
		根据offset和limit获取收藏插画的pid
		:params offset: 偏移量
		:params rest: 公开/未公开收藏
		:params raw: 是否返回原始数据
		:return :对应offset和limit的pid列表,int类型
		"""
		params = {
			"tag": "",
			"offset": offset,
			"limit": self.bookmark_page_offset,
			"rest": rest,
		}
		try:
			r = json.loads(self.base_request({"url":self.bookmark_url},params=params).text)
		except Exception as e:
			# 网络请求出错
			logger.debug("{}: 分页请求异常 offset={}: {}", self.class_name, offset, e)
			return None
		else:
			# 未登录
			if r["message"] == TEMP_MSG["UNLOGIN_TEXT"]:
				return TEMP_MSG["UL_TEXT"]

			if raw:
				return r
			res = r["body"]["works"]
			illusts_pid = [int(i["id"]) for i in res]
			return illusts_pid

	def check_update(self):
		"""Return changed, unchanged or error after checking every enabled list."""
		if hasattr(self.db, "pool") is False:
			logger.warning(TEMP_MSG["UPDATE_INFO"].format(self.class_name))
			return "changed"
		responses = []
		for rest in self.rest_list:
			response = self.get_page_bookmark(offset=0, rest=rest, raw=True)
			if response == TEMP_MSG["UL_TEXT"] or not isinstance(response, dict) or response.get("error"):
				logger.warning(TEMP_MSG["UPDATE_CHECK_ERROR_INFO"].format(self.class_name))
				return "error"
			responses.append(response)
		for response in responses:
			works = response.get("body", {}).get("works", [])
			if not isinstance(works, list):
				return "error"
			for work in works[:10]:
				try:
					exists, _ = self.db.check_illust(int(work["id"]), table="bookmark", strict=True)
				except TypeError:
					exists, _ = self.db.check_illust(int(work["id"]), table="bookmark")
				except Exception:
					return "error"
				if not exists:
					logger.success(TEMP_MSG["UPDATE_INFO"].format(self.class_name))
					return "changed"
		if all(not response.get("body", {}).get("works", []) for response in responses):
			logger.warning(TEMP_MSG["UPDATE_CHECK_EMPTY_INFO"].format(self.class_name))
		else:
			logger.warning(TEMP_MSG["UPDATE_CANLE_INFO"].format(self.class_name))
		return "unchanged"

	def thread_by_illust(self, *args):
		"""
		线程任务函数
		"""
		pid = args[0]
		info = None

		# 基于数据库的提前跳过，避免网络与文件系统检测
		if hasattr(self.db, "pool") and SKIP_DB_EXISTS_ILLUST:
			try:
				exists, _ = self.db.check_illust(pid, table="bookmark")
				if exists:
					self._count_run_stat('skipped_existing')
					logger.info(f"SKIP_DB_EXISTS_ILLUST - {pid}")
					return info
			except Exception:
				pass

		# 跳过已下载插画的请求
		if SKIP_ISEXISTS_ILLUST and self.file_manager.search_isExistsPid(
			BOOKMARK_PATH,"b",*(pid,)):
			self._count_run_stat('skipped_file')
			logger.info(f"SKIP_ISEXISTS_ILLUST - {pid}")
			return info

		try:
			info = self.Downloader.get_illust_info(pid,extra="bookmark")
		except Exception as e:
			self._count_run_stat('failed')
			logger.warning(TEMP_MSG["ILLUST_NETWORK_ERROR_INFO"].format(self.class_name,pid,e))
			logger.warning(f"{pid} INFO:{info}")
			return info

		if not info: 
			self._count_run_stat('failed')
			logger.warning(TEMP_MSG["ILLUST_EMPTY_INFO"].format(self.class_name,pid))
			return info

		if info == TEMP_MSG["LIMIT_TEXT"]:
			logger.warning(TEMP_MSG["LIMIT_TEXT_RESP"].format(pid))
			timer = Timer()
			timer.Downloader = self.Downloader
			timer.pid = pid
			timer.extra = "bookmark"
			try:
				info = timer.waiting(info, stop_event=getattr(self, "_stop_event", None))
			except Exception as e:
				logger.warning(f"Exception - {e} - {info}")
			logger.success(f"共休眠{timer._time}秒,重新恢复访问")

		# 远端不可访问结果不作为成功元数据；保留已有归档。
		if info == TEMP_MSG["PID_DELETED_TEXT"] or info == TEMP_MSG["PID_UNAUTH_ACCESS"] or info == TEMP_MSG["PID_UNAUTH_ACCESS_2"]:
			self._count_run_stat('deleted')
			return info
		if isinstance(info, str):
			self._count_run_stat('failed')
			return info

		# 数据库开关关闭
		if hasattr(self.db,"pool") == False:
			self._count_run_stat('success')
			return info

		try:
			isExists,path = self.db.check_illust(pid,table="bookmark")
			# 数据库无该记录
			if isExists == False:
				# 第一次更新,pid不存在/被删除/无法找到
				if info == TEMP_MSG["PID_DELETED_TEXT"] or info == TEMP_MSG["PID_ERROR_TEXT"]:
					self._count_run_stat('deleted')
					return info
					
				res = self.db.insert_illust(info,table="bookmark")
				if res:
					self._count_run_stat('success')
					logger.success(TEMP_MSG["INSERT_SUCCESS_INFO"].format(self.class_name,pid))
				else:
					self._count_run_stat('failed')
					logger.warning(TEMP_MSG["INSERT_FAIL_INFO"].format(self.class_name,pid))
			# 数据库有该记录
			else:
				# 远端不可访问时保留归档记录和本地文件，避免丢失历史元数据。
				if info == TEMP_MSG["PID_DELETED_TEXT"] or\
					info == TEMP_MSG["PID_UNAUTH_ACCESS"] or\
					info == TEMP_MSG["PID_UNAUTH_ACCESS_2"]:
					logger.warning(f"{self.class_name}: 保留不可访问作品记录 - PID:{pid} - {info}")
					self._count_run_stat('deleted')
					return info
				else:
					if not self.db.update_illust(info,table="bookmark"):
						raise RuntimeError("更新图片数据库记录失败")
					self._count_run_stat('success')
		except Exception as e:
			self._count_run_stat('failed')
			logger.warning("thread_by_illust|Exception {}".format(e))
			logger.warning(f"{pid} INFO:{info}")

	def run_once(self, stop_event=None, deadline=None):
		if stop_event is None:
			stop_event = threading.Event()
		self._stop_event = stop_event
		self.Downloader.stop_event = stop_event
		self._run_stats = {
			"success": 0, "skipped_existing": 0, "skipped_file": 0,
			"failed": 0, "deleted": 0, "page_failed": 0, "aborted": False,
		}
		self._run_stats_lock = threading.Lock()
		run_stats = self._run_stats
		pool = None
		result = PhaseResult(self.class_name.lower())
		# 启动一致性检查（最近 20 条）
		try:
			check_stats = run_startup_check(self.Downloader) or {}
			for key in ("failed", "query_failed"):
				if check_stats.get(key):
					result.add_error("图片一致性检查{} {} 条".format(key, check_stats[key]))
		except Exception as e:
			check_stats = {"error": 1}
			result.add_error("图片一致性检查异常: {}".format(e))
			logger.warning(f"启动一致性检查失败: {e}")

		# TDOD TAG COUNT开始工作
		TAG_FLAG_BOOKMARK = False
		logger.info(TEMP_MSG["BEGIN_INFO"].format(self.class_name))
		# 更新机制判定
		update_state = self.check_update()
		if update_state in (False, 'unchanged'):
			result.unchanged = True
			self._log_run_summary()
			result.barrier_passed = True
			result.cleanup_complete = True
			result.counts.update(self._run_stats)
			return result
		if update_state in (None, 'error'):
			result.add_error("收藏更新检查失败")
			logger.warning("{}: 收藏更新检查失败，跳过图片分页但继续完成阶段边界".format(self.class_name))
			result.counts.update(self._run_stats)
			result.barrier_passed = True
			result.cleanup_complete = True
			return result

		try:
			offset = 0
			pool = ThreadPool(8)
			# 分别获取公开/非公开收藏插画
			for rest in self.rest_list:
				if stop_event.is_set():
					break
				logger.info(f"===== 开始获取{self.rest_dict[rest]}收藏 =====")
				offset = 0
				while True:
					if stop_event.is_set() or (deadline is not None and time.monotonic() >= deadline):
						break
					pid_list = self.get_page_bookmark(offset, rest)
					# 获取异常返回None
					if pid_list == None:
						run_stats["page_failed"] += 1
						logger.warning(TEMP_MSG["BOOKMARK_PAGE_ERROR_INFO"].format(
							self.class_name, self.rest_dict[rest],
							offset, offset+self.bookmark_page_offset
						))
						break
					
					# 未登录
					if pid_list == TEMP_MSG["UL_TEXT"]:
						logger.warning(TEMP_MSG["UNLOGIN_INFO"].format(self.class_name))
						break

					# 无收藏返回[]
					if pid_list == []:
						logger.warning(TEMP_MSG["BOOKMARK_PAGE_EMPTY_INFO"].format(self.class_name,
							offset, offset+self.bookmark_page_offset))
						offset = 0
						break

					logger.info(TEMP_MSG["BOOKMARK_NOW_INFO"].format(offset,offset+self.bookmark_page_offset,len(pid_list)))
					for pid in pid_list:
						if not pool.put(self.thread_by_illust, (pid,), self._task_callback, stop_event=stop_event, deadline=deadline):
							self._count_run_stat('failed')

					offset += self.bookmark_page_offset
					# 取消固定/随机等待，统一交由 Downloader 自适应限速（仅对线上接口生效）
		except Exception as e:
			run_stats["aborted"] = True
			logger.warning("Exception {}".format(e))
		finally:
			# 累计等于5次,不触发更新限制机制,全更新完后恢复day_count
			if self.day_count == self.day_all_update_num:
				logger.success(TEMP_MSG["UPDATE_DAY_ALL_INFO"].format(self.class_name))
				self.day_count = 0
			else:
				self.day_count += 1

			if pool is not None:
				pool.close()
				joined = pool.join(stop_event=stop_event, deadline=deadline)
				for kind, error in pool.errors:
					result.add_error("线程池{}异常: {}".format(kind, error))
				if not joined:
					run_stats["aborted"] = True
					result.reason = "线程池未在阶段预算内收尾"
					# 非 daemon worker 不能遗留到 IMAGE_PHASE_DONE 之后；预算失败
					# 只影响阶段结果，仍等待正在执行的请求安全结束。
					pool.join()
			# TDOD TAG COUNT完成工作
			TAG_FLAG_BOOKMARK = True
		logger.info("="*48)
		self._log_run_summary()
		result.counts.update(run_stats)
		result.errors.extend(["图片任务失败 {} 次".format(run_stats["failed"])] if run_stats["failed"] else [])
		result.errors.extend(["分页失败 {} 次".format(run_stats["page_failed"])] if run_stats["page_failed"] else [])
		if stop_event.is_set():
			result.status = "stopped"
			result.barrier_passed = False
		elif result.reason or run_stats["aborted"]:
			result.status = "failed"
			result.barrier_passed = False
		else:
			result.barrier_passed = True
		result.cleanup_complete = True
		return result

	def run(self):
		return self.run_once()


# if __name__ == '__main__':
# 	from config import BOOKMARK_CYCLE
# 	b = Bookmark()
# 	while True:
# 		b.run()
# 		time.sleep(BOOKMARK_CYCLE)
