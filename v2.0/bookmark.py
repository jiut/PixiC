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
		"""
		检查是否更新,获取收藏第一页前十个插画的id
		更新机制:
			获取最新收藏的10条插画id,与数据库中的记录进行比对
			若最新收藏的10条插画id有一条在数据库中,则跳过;若不在则更新
			实际上是以最快10分钟内收藏10条新作品这个标准作为界限
		"""

		# 数据库开关若关闭,直接更新
		if hasattr(self.db,"pool") == False:
			logger.warning(TEMP_MSG["UPDATE_INFO"].format(self.class_name))
			return True

		# 判断非公开及公开
		res = self.get_page_bookmark(offset=0, raw=True)
		if BOOKMARK_HIDE_ENABLE:
			res_hide = self.get_page_bookmark(offset=0, rest="hide", raw=True)

		# 未登录
		if res == TEMP_MSG["UL_TEXT"]:
			logger.warning(TEMP_MSG["UPDATE_CHECK_ERROR_INFO"].format(self.class_name))
			return False

		# 公开收藏 - 错误
		if not isinstance(res, dict) or res.get("error"):
			logger.warning(TEMP_MSG["UPDATE_CHECK_ERROR_INFO"].format(self.class_name))
			logger.warning(f"<res> - {res}")
			return False

		# 验证前十张
		for _ in res["body"]["works"][:10]:
			if not self.db.check_illust(int(_["id"]),table="bookmark")[0]:
				logger.success(TEMP_MSG["UPDATE_INFO"].format(self.class_name))
				return True
		
		if BOOKMARK_HIDE_ENABLE:
			# 未公开收藏 - 错误/无权限
			if not isinstance(res_hide, dict) or res_hide.get("error"):
				if isinstance(res_hide, dict) and TEMP_MSG["NO_AUTH"] in res_hide.get("message", ""):
					logger.warning(TEMP_MSG["UPDATE_CHECK_NO_AUTH_INFO"].format(
						self.class_name, self.user_id, self.user_id))
				else:
					logger.warning(TEMP_MSG["UPDATE_CHECK_ERROR_INFO"].format(self.class_name))
				logger.warning(f"<res_hide> - {res_hide}")
				return False
			# 作品为空
			if not res["body"]["works"] and not res_hide["body"]["works"]:
				logger.warning(TEMP_MSG["UPDATE_CHECK_EMPTY_INFO"].format(self.class_name))
				return False
			# 验证前十张
			for _ in res_hide["body"]["works"][:10]:
				if not self.db.check_illust(int(_["id"]),table="bookmark")[0]:
					logger.success(TEMP_MSG["UPDATE_INFO"].format(self.class_name))
					return True
		
		logger.warning(TEMP_MSG["UPDATE_CANLE_INFO"].format(self.class_name))
		return False

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
				info = timer.waiting(info)
			except Exception as e:
				logger.warning(f"Exception - {e} - {info}")
			logger.success(f"共休眠{timer._time}秒,重新恢复访问")

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
					self.db.update_illust(info,table="bookmark")
					self._count_run_stat('success')
		except Exception as e:
			self._count_run_stat('failed')
			logger.warning("thread_by_illust|Exception {}".format(e))
			logger.warning(f"{pid} INFO:{info}")

	@logger.catch
	def run(self):
		self._run_stats = {
			"success": 0, "skipped_existing": 0, "skipped_file": 0,
			"failed": 0, "deleted": 0, "page_failed": 0, "aborted": False,
		}
		self._run_stats_lock = threading.Lock()
		run_stats = self._run_stats
		pool = None
		# 启动一致性检查（最近 20 条）
		try:
			run_startup_check(self.Downloader)
		except Exception as e:
			logger.warning(f"启动一致性检查失败: {e}")

		# TDOD TAG COUNT开始工作
		TAG_FLAG_BOOKMARK = False
		logger.info(TEMP_MSG["BEGIN_INFO"].format(self.class_name))
		# 更新机制判定
		if self.check_update() == False:
			self._log_run_summary()
			logger.info(TEMP_MSG["SLEEP_INFO"].format(self.class_name))
			return

		try:
			offset = 0
			pool = ThreadPool(8)
			# 分别获取公开/非公开收藏插画
			for rest in self.rest_list:
				logger.info(f"===== 开始获取{self.rest_dict[rest]}收藏 =====")
				while True:
					# 累计更新小于5次,更新前800张(实际为768) TODO
					if self.day_count < self.day_all_update_num:
						if offset > self.day_limit:
							logger.info(TEMP_MSG["UPDATE_DAY_LIMIT_INFO"].format(self.class_name,self.day_limit,self.day_count))
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
						pool.put(self.thread_by_illust,(pid,),callback)

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
			# TDOD TAG COUNT完成工作
			TAG_FLAG_BOOKMARK = True
		logger.info("="*48)
		self._log_run_summary()
		logger.info(TEMP_MSG["SLEEP_INFO"].format(self.class_name))


# if __name__ == '__main__':
# 	from config import BOOKMARK_CYCLE
# 	b = Bookmark()
# 	while True:
# 		b.run()
# 		time.sleep(BOOKMARK_CYCLE)
