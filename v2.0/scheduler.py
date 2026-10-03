# coding=utf8
"""One-shot coordinator for the image and novel phases."""

import os
import inspect
import signal
import sys
import threading
import time
import uuid

from config import (
    API_HOST,
    API_PORT,
    DB_ENABLE,
    PIXIV_API_ENABLED,
    PIXIV_BOOKMARK_ENABLED,
    PIXIV_CRAWLER_ENABLED,
    PIXIV_NOVEL_ENABLED,
    BATCH_PHASE_DEADLINE_SECONDS,
)
from instance_lock import InstanceLock
from log_record import complete_phase, logger, set_log_context
from message import TEMP_MSG
from phase_result import (
    BatchResult,
    EXIT_ALREADY_RUNNING,
    EXIT_INIT_FAILED,
    EXIT_STOPPED,
    PhaseResult,
)


class Scheduler(object):
    def __init__(self, config=None):
        # An explicit empty mapping is a valid all-disabled test/configuration.
        self.config = self._config_from_module() if config is None else dict(config)
        self.stop_event = threading.Event()
        self._old_handlers = {}

    @staticmethod
    def _config_from_module():
        return {
            "api_host": API_HOST,
            "api_port": API_PORT,
            "db_enable": DB_ENABLE,
            "api_enabled": PIXIV_API_ENABLED,
            "bookmark_enabled": PIXIV_BOOKMARK_ENABLED,
            "crawler_enabled": PIXIV_CRAWLER_ENABLED,
            "novel_enabled": PIXIV_NOVEL_ENABLED,
            "phase_deadline_seconds": BATCH_PHASE_DEADLINE_SECONDS,
        }

    def _install_stop_handlers(self):
        def request_stop(signum, _frame):
            self.stop_event.set()

        for signum in (signal.SIGINT, getattr(signal, "SIGTERM", None)):
            if signum is None:
                continue
            try:
                self._old_handlers[signum] = signal.getsignal(signum)
                signal.signal(signum, request_stop)
            except (ValueError, OSError):
                continue

    def _restore_stop_handlers(self):
        for signum, handler in self._old_handlers.items():
            try:
                signal.signal(signum, handler)
            except (ValueError, OSError):
                pass
        self._old_handlers.clear()

    def _conflicting_services(self):
        return self.config.get("crawler_enabled") or self.config.get("api_enabled")

    def _load_login(self):
        # Importing login initializes Selenium/client state, so keep it after the lock.
        from login import client
        return client

    @staticmethod
    def _invoke_phase(method, stop_event, deadline):
        """兼容旧 run() 与新 run_once，避免 TypeError 后重复执行。"""
        try:
            parameters = inspect.signature(method).parameters
        except (TypeError, ValueError):
            parameters = {}
        accepts_kwargs = any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        )
        # Loguru's legacy @logger.catch wrapper can expose *args/**kwargs even
        # when the wrapped run() accepts no arguments. Treat that method as
        # the old no-argument contract to avoid swallowing an argument error.
        if accepts_kwargs and getattr(method, "__name__", "") != "run":
            return method(stop_event=stop_event, deadline=deadline)
        kwargs = {}
        if "stop_event" in parameters:
            kwargs["stop_event"] = stop_event
        if "deadline" in parameters:
            kwargs["deadline"] = deadline
        if kwargs:
            return method(**kwargs)
        return method()

    @staticmethod
    def _coerce_phase_result(obj, result, stop_event):
        """将旧版统计字典转换为统一阶段结果，不丢失单项错误。"""
        if isinstance(result, PhaseResult):
            return result
        stats = result if isinstance(result, dict) else getattr(obj, "_run_stats", None)
        if not isinstance(stats, dict):
            stats = {}
        counts = {
            key: value for key, value in stats.items()
            if isinstance(value, int) and not isinstance(value, bool)
        }
        error_keys = (
            "failed", "page_failed", "check_failed", "integrity_failed",
            "integrity_query_failed", "aborted",
        )
        errors = ["{}: {}".format(key, stats[key]) for key in error_keys if stats.get(key)]
        stopped = stop_event.is_set()
        aborted = bool(stats.get("aborted"))
        # 单项失败只影响最终状态；只有中止或协作停止不能建立屏障。
        status = "stopped" if stopped else ("failed" if aborted else "completed")
        return PhaseResult(
            name=obj.__class__.__name__.lower(),
            status=status,
            barrier_passed=not stopped and not aborted,
            cleanup_complete=True,
            counts=counts,
            errors=errors,
            reason="stop requested" if stopped else ("phase aborted" if aborted else None),
        )

    @classmethod
    def _run_phase(cls, obj, stop_event, deadline=None):
        method = getattr(obj, "run_once", None) or obj.run
        try:
            result = cls._invoke_phase(method, stop_event, deadline)
            phase_result = cls._coerce_phase_result(obj, result, stop_event)
        except SystemExit as exc:
            phase_result = PhaseResult(obj.__class__.__name__.lower(), "failed", reason=str(exc))
            phase_result.add_error(exc)
        except Exception as exc:
            phase_result = PhaseResult(obj.__class__.__name__.lower(), "failed", reason=str(exc))
            phase_result.add_error(exc)
        return phase_result

    def scheduler_bookmark(self, stop_event=None, limit=None, deadline=None):
        from bookmark import Bookmark
        obj = Bookmark()
        self._active_phase_obj = obj
        return self._run_phase(obj, stop_event or self.stop_event, deadline)

    def scheduler_novel(self, stop_event=None, limit=None, deadline=None):
        from novel import Novel
        obj = Novel()
        self._active_phase_obj = obj
        return self._run_phase(obj, stop_event or self.stop_event, deadline)

    def _phase_deadline(self):
        seconds = self.config.get("phase_deadline_seconds")
        if seconds is None:
            return None
        try:
            seconds = float(seconds)
        except (TypeError, ValueError):
            raise ValueError("phase_deadline_seconds 必须为数字或 None")
        if seconds <= 0:
            raise ValueError("phase_deadline_seconds 必须大于 0")
        return time.monotonic() + seconds

    def _schedule_with_deadline(self, method, stop_event, deadline):
        try:
            parameters = inspect.signature(method).parameters
        except (TypeError, ValueError):
            parameters = {}
        if "deadline" in parameters or any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in parameters.values()):
            return method(stop_event, deadline=deadline)
        return method(stop_event)

    @staticmethod
    def _close_resource(resource):
        if resource is None:
            return
        close = getattr(resource, "close", None)
        if callable(close):
            close()

    def _close_phase_resources(self):
        """关闭阶段专属网络、数据库资源；关闭错误阻断阶段屏障。"""
        obj = getattr(self, "_active_phase_obj", None)
        self._active_phase_obj = None
        if obj is None:
            return []
        errors = []
        downloader = getattr(obj, "Downloader", None) or getattr(obj, "downloader", None)
        resources = [downloader]
        if downloader is not None:
            resources.extend([getattr(downloader, "se", None), getattr(downloader, "session", None)])
            db = getattr(downloader, "db", None)
            if db is not None:
                resources.extend([getattr(db, "pool", None), db])
        resources.extend([getattr(obj, "db", None)])
        seen = set()
        for resource in resources:
            if resource is None or id(resource) in seen:
                continue
            seen.add(id(resource))
            try:
                self._close_resource(resource)
            except Exception as exc:
                errors.append(str(exc))
        return errors

    def _validate_config(self):
        if self.config.get("api_enabled") and not self.config.get("db_enable"):
            return "API 已启用但数据库未启用"
        if self._conflicting_services():
            return "crawler/API 常驻服务与严格批次顺序冲突"
        return None

    @staticmethod
    def _wait_for_interactive_exit():
        """交互式终端中保留窗口，重定向/计划任务运行时直接结束。"""
        try:
            interactive = bool(sys.stdin.isatty() and sys.stdout.isatty())
        except (AttributeError, OSError):
            interactive = False
        if not interactive:
            return
        try:
            sys.stderr.write("PixiC 批次已完成，按 Enter 退出...\n")
            sys.stderr.flush()
            input()
        except (EOFError, KeyboardInterrupt):
            return

    def run(self):
        config_error = self._validate_config()
        if config_error:
            sys.stderr.write("PixiC 配置错误: {}\n".format(config_error))
            sys.stderr.flush()
            return EXIT_INIT_FAILED

        lock_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".pixic-batch.lock")
        lock = InstanceLock(lock_path)
        if not lock.acquire():
            sys.stderr.write("PixiC 批次已有实例运行\n")
            sys.stderr.flush()
            return EXIT_ALREADY_RUNNING

        batch = BatchResult()
        try:
            self._install_stop_handlers()
            batch_id = uuid.uuid4().hex
            set_log_context(batch_id, "preamble")
            logger.success(TEMP_MSG["VERSION_INFO"])

            if self.config.get("bookmark_enabled") or self.config.get("novel_enabled"):
                try:
                    login = self._load_login()
                    try:
                        login.check(stop_event=self.stop_event, deadline=self._phase_deadline())
                    except TypeError:
                        login.check()
                except SystemExit as exc:
                    batch.add_error("登录初始化退出: {}".format(exc))
                    return EXIT_INIT_FAILED
                except Exception as exc:
                    batch.add_error("登录初始化失败: {}".format(exc))
                    return EXIT_INIT_FAILED

            if self.config.get("bookmark_enabled"):
                try:
                    set_log_context(batch_id, "images")
                    batch.image = self._schedule_with_deadline(
                        self.scheduler_bookmark, self.stop_event, self._phase_deadline())
                except SystemExit as exc:
                    batch.image = PhaseResult("bookmark", "failed", reason=str(exc))
                    batch.image.reason = "初始化失败: {}".format(exc)
                    batch.image.init_failed = True
                    batch.image.add_error(exc)
                except Exception as exc:
                    batch.image = PhaseResult("bookmark", "failed", reason=str(exc))
                    batch.image.reason = "初始化失败: {}".format(exc)
                    batch.image.init_failed = True
                    batch.image.add_error(exc)
            else:
                set_log_context(batch_id, "images")
                batch.image = PhaseResult.disabled("bookmark")

            image_close_errors = self._close_phase_resources()
            if image_close_errors and batch.image is not None:
                batch.image.output_healthy = False
                batch.image.barrier_passed = False
                batch.image.status = "failed"
                batch.image.add_error("图片阶段资源关闭失败: {}".format("; ".join(image_close_errors)))

            set_log_context(batch_id, "images")
            logger.info("IMAGE_PHASE_SUMMARY | status={} errors={}",
                        batch.image.status if batch.image else "disabled",
                        len(batch.image.errors) if batch.image else 0)

            if batch.image and batch.image.barrier_passed and not self.stop_event.is_set():
                sink_errors = complete_phase()
                if sink_errors:
                    batch.image.output_healthy = False
                    batch.image.barrier_passed = False
                    batch.image.status = "failed"
                    batch.image.add_error("图片日志 sink 写入失败")
                if batch.image.barrier_passed:
                    set_log_context(batch_id, "images")
                    logger.info("IMAGE_PHASE_DONE")
                    marker_errors = complete_phase()
                    if marker_errors:
                        batch.image.output_healthy = False
                        batch.image.barrier_passed = False
                        batch.image.status = "failed"
                        batch.image.add_error("图片完成标记日志写入失败")
                if batch.image.barrier_passed and self.config.get("novel_enabled"):
                    try:
                        set_log_context(batch_id, "novels")
                        batch.novel = self._schedule_with_deadline(
                            self.scheduler_novel, self.stop_event, self._phase_deadline())
                    except SystemExit as exc:
                        batch.novel = PhaseResult("novel", "failed", reason="初始化失败: {}".format(exc))
                        batch.novel.init_failed = True
                        batch.novel.add_error(exc)
                    except Exception as exc:
                        batch.novel = PhaseResult("novel", "failed", reason="初始化失败: {}".format(exc))
                        batch.novel.init_failed = True
                        batch.novel.add_error(exc)
                    novel_close_errors = self._close_phase_resources()
                    if novel_close_errors and batch.novel is not None:
                        batch.novel.output_healthy = False
                        batch.novel.barrier_passed = False
                        batch.novel.status = "failed"
                        batch.novel.add_error("小说阶段资源关闭失败: {}".format("; ".join(novel_close_errors)))
                    set_log_context(batch_id, "novels")
                    logger.info("NOVEL_PHASE_SUMMARY | status={} errors={}",
                                batch.novel.status if batch.novel else "disabled",
                                len(batch.novel.errors) if batch.novel else 0)
                    if batch.novel and batch.novel.barrier_passed:
                        logger.info("NOVEL_PHASE_DONE")
                        novel_marker_errors = complete_phase()
                        if novel_marker_errors:
                            batch.novel.output_healthy = False
                            batch.novel.barrier_passed = False
                            batch.novel.status = "failed"
                            batch.novel.add_error("小说完成标记日志写入失败")
                else:
                    batch.novel = PhaseResult.disabled("novel")
            elif self.config.get("novel_enabled"):
                batch.novel = PhaseResult("novel", "failed", reason="图片阶段未建立完成边界")
                batch.novel.barrier_passed = False
                batch.novel.add_error("图片阶段未建立完成边界")

            # 即使小说被跳过，也释放可能由自定义 helper 创建的资源。
            if not self.config.get("novel_enabled") or batch.novel is None:
                self._close_phase_resources()

            set_log_context(batch_id, "batch")
            logger.info("BATCH_SUMMARY | 图片状态={} 小说状态={}",
                        batch.image.status if batch.image else "disabled",
                        batch.novel.status if batch.novel else "disabled")

            # Drain once more after the last phase: an asynchronous file sink
            # may report a write error while consuming the final summary.
            final_sink_errors = complete_phase()
            if final_sink_errors:
                affected = batch.novel if batch.novel and batch.novel.enabled else batch.image
                if affected is not None:
                    affected.output_healthy = False
                    affected.barrier_passed = False
                    affected.status = "failed"
                    affected.add_error("批次日志 sink 写入失败")

            self._wait_for_interactive_exit()
            if self.stop_event.is_set() and batch.exit_code == 0:
                return EXIT_STOPPED
            return batch.finalize()
        finally:
            try:
                complete = getattr(logger, "complete", None)
                if complete:
                    complete()
            finally:
                self._restore_stop_handlers()
                lock.release()


if __name__ == "__main__":
    sys.exit(Scheduler().run())
