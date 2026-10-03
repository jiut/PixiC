import multiprocessing
import os
import signal
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from instance_lock import InstanceLock
from phase_result import BatchResult, EXIT_FAILED, EXIT_INIT_FAILED, EXIT_STOPPED, PhaseResult
from scheduler import Scheduler


def _hold_lock(path, ready, release):
    lock = InstanceLock(path)
    ready.put(lock.acquire())
    release.get()
    lock.release()


class SchedulerContractTest(unittest.TestCase):
    def test_phase_order_markers_and_deadline(self):
        events = []

        class FakeScheduler(Scheduler):
            def _load_login(self):
                return Mock(check=Mock())

            def scheduler_bookmark(self, stop_event=None, deadline=None):
                events.append(("image", deadline))
                return PhaseResult("bookmark", status="completed", barrier_passed=True)

            def scheduler_novel(self, stop_event=None, deadline=None):
                events.append(("novel", deadline))
                return PhaseResult("novel", status="completed", barrier_passed=True)

        config = {"db_enable": True, "api_enabled": False, "crawler_enabled": False,
                  "bookmark_enabled": True, "novel_enabled": True,
                  "phase_deadline_seconds": 30}
        logger = Mock()
        ordered = []
        logger.info.side_effect = lambda message, *args: ordered.append(message)
        with patch("scheduler.complete_phase", side_effect=lambda: ordered.append("COMPLETE_PHASE") or []), patch("scheduler.logger", logger):
            result = FakeScheduler(config).run()
        self.assertEqual(result, 0)
        self.assertEqual([item[0] for item in events], ["image", "novel"])
        self.assertTrue(all(item[1] is not None for item in events))
        messages = ordered
        self.assertLess(messages.index("IMAGE_PHASE_DONE"), messages.index("NOVEL_PHASE_DONE"))
        novel_done = messages.index("NOVEL_PHASE_DONE")
        batch_summary = messages.index("BATCH_SUMMARY | 图片状态={} 小说状态={}")
        self.assertTrue(any(novel_done < index < batch_summary
                            for index, message in enumerate(messages)
                            if message == "COMPLETE_PHASE"))

    def test_disabled_image_still_uses_image_context_before_novel(self):
        contexts = []

        class FakeScheduler(Scheduler):
            def _load_login(self):
                return Mock(check=Mock())

            def scheduler_novel(self, stop_event=None, deadline=None):
                contexts.append("novel")
                return PhaseResult("novel", status="completed", barrier_passed=True)

        config = {"db_enable": True, "api_enabled": False, "crawler_enabled": False,
                  "bookmark_enabled": False, "novel_enabled": True}
        with patch("scheduler.set_log_context", side_effect=lambda _batch, phase: contexts.append(phase)), \
             patch("scheduler.complete_phase", return_value=[]), patch("scheduler.logger", Mock()):
            self.assertEqual(FakeScheduler(config).run(), 0)
        self.assertIn("images", contexts)
        self.assertIn("novels", contexts)

    def test_init_failure_maps_to_init_exit(self):
        class FakeScheduler(Scheduler):
            def _load_login(self):
                raise RuntimeError("bad login")

        config = {"db_enable": True, "api_enabled": False, "crawler_enabled": False,
                  "bookmark_enabled": True, "novel_enabled": False}
        with patch("scheduler.logger", Mock()), patch("scheduler.complete_phase", return_value=[]):
            self.assertEqual(FakeScheduler(config).run(), EXIT_INIT_FAILED)

    def test_signal_handler_only_requests_stop(self):
        scheduler = Scheduler({"bookmark_enabled": False, "novel_enabled": False,
                               "db_enable": False, "api_enabled": False,
                               "crawler_enabled": False})
        with patch("scheduler.signal.getsignal", return_value=signal.SIG_DFL), \
             patch("scheduler.signal.signal") as install:
            scheduler._install_stop_handlers()
            handler = install.call_args_list[0].args[1]
            handler(signal.SIGINT, None)
            self.assertTrue(scheduler.stop_event.is_set())
        scheduler._restore_stop_handlers()

    def test_noninteractive_batch_does_not_pause(self):
        with patch("scheduler.sys.stdin") as stdin, patch("scheduler.sys.stdout") as stdout, \
             patch("scheduler.input") as read_input:
            stdin.isatty.return_value = False
            stdout.isatty.return_value = False
            Scheduler._wait_for_interactive_exit()
        read_input.assert_not_called()
    def test_recoverable_image_error_still_runs_novel(self):
        events = []

        class FakeScheduler(Scheduler):
            def _load_login(self):
                return Mock(check=Mock())

            def scheduler_bookmark(self, stop_event=None):
                events.append("image")
                result = PhaseResult("bookmark", status="completed", barrier_passed=True)
                result.add_error("one image failed")
                return result

            def scheduler_novel(self, stop_event=None):
                events.append("novel")
                return PhaseResult("novel", status="completed", barrier_passed=True)

        config = {"db_enable": True, "api_enabled": False, "crawler_enabled": False,
                  "bookmark_enabled": True, "novel_enabled": True}
        with patch("scheduler.complete_phase", return_value=[]), patch("scheduler.logger", Mock()):
            result = FakeScheduler(config).run()
        self.assertEqual(events, ["image", "novel"])
        self.assertEqual(result, EXIT_FAILED)

    def test_fatal_image_barrier_skips_novel(self):
        events = []

        class FakeScheduler(Scheduler):
            def _load_login(self):
                return Mock(check=Mock())

            def scheduler_bookmark(self, stop_event=None):
                events.append("image")
                return PhaseResult("bookmark", status="failed", barrier_passed=False)

            def scheduler_novel(self, stop_event=None):
                events.append("novel")
                return PhaseResult("novel", status="completed", barrier_passed=True)

        config = {"db_enable": True, "api_enabled": False, "crawler_enabled": False,
                  "bookmark_enabled": True, "novel_enabled": True}
        with patch("scheduler.complete_phase", return_value=[]), patch("scheduler.logger", Mock()):
            FakeScheduler(config).run()
        self.assertEqual(events, ["image"])

    def test_phase_result_allows_item_errors_after_barrier(self):
        image = PhaseResult("bookmark", status="completed", barrier_passed=True)
        image.add_error("one item failed")
        batch = BatchResult(image=image)
        self.assertEqual(batch.finalize(), EXIT_FAILED)
        self.assertTrue(image.recoverable_errors)

    def test_single_instance_lock_competes_across_processes(self):
        path = os.path.join(tempfile.gettempdir(), "pixic-lock-{}.lock".format(os.getpid()))
        try:
            ready = multiprocessing.Queue()
            release = multiprocessing.Queue()
        except PermissionError as exc:
            self.skipTest("multiprocessing primitives unavailable: {}".format(exc))
        process = multiprocessing.Process(target=_hold_lock, args=(path, ready, release))
        process.start()
        try:
            self.assertTrue(ready.get(timeout=5))
            contender = InstanceLock(path)
            self.assertFalse(contender.acquire())
            release.put(True)
            process.join(timeout=5)
            self.assertEqual(process.exitcode, 0)
            self.assertTrue(contender.acquire())
            contender.release()
        finally:
            if process.is_alive():
                release.put(True)
                process.join(timeout=5)
            try:
                os.remove(path)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
