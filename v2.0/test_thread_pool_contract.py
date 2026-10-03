import threading
import time
import unittest

from thread_pool import ThreadPool


class ThreadPoolContractTest(unittest.TestCase):
    def test_close_join_stops_real_workers_and_rejects_late_work(self):
        pool = ThreadPool(1, max_task_num=3)
        self.assertTrue(pool.put(lambda: "ok", ()))
        pool.close()
        self.assertFalse(pool.put(lambda: "late", ()))
        self.assertTrue(pool.join())
        self.assertEqual(pool.generate_list, [])
        self.assertEqual(pool.stats["queued"], 0)

    def test_join_waits_for_callback(self):
        callback_done = threading.Event()
        pool = ThreadPool(2)

        def work():
            return "ok"

        def callback(status, result):
            self.assertTrue(status)
            self.assertEqual(result, "ok")
            time.sleep(0.03)
            callback_done.set()

        self.assertTrue(pool.put(work, (), callback))
        pool.close()
        self.assertTrue(pool.join())
        self.assertTrue(callback_done.is_set())
        self.assertEqual(pool.stats["active"], 0)

    def test_task_and_callback_errors_are_collected(self):
        pool = ThreadPool(1)

        def work():
            raise ValueError("task")

        def callback(status, result):
            raise RuntimeError("callback")

        self.assertTrue(pool.put(work, (), callback))
        pool.close()
        self.assertTrue(pool.join())
        self.assertEqual([kind for kind, _ in pool.errors], ["task", "callback"])

    def test_terminate_cancels_queued_callbacks(self):
        started = threading.Event()
        release = threading.Event()
        statuses = []
        pool = ThreadPool(1, max_task_num=2)

        def work():
            started.set()
            release.wait(1)
            return "active"

        def record(status, result):
            statuses.append((status, result))

        self.assertTrue(pool.put(work, (), record))
        self.assertTrue(started.wait(1))
        self.assertTrue(pool.put(lambda: "queued", (), record))
        pool.terminate()
        release.set()
        self.assertTrue(pool.join())
        self.assertIn((False, None), statuses)
        self.assertEqual(pool.stats["cancelled"], 1)

    def test_deadline_does_not_claim_active_worker_finished(self):
        release = threading.Event()
        pool = ThreadPool(1)

        def work():
            release.wait(1)

        self.assertTrue(pool.put(work, ()))
        pool.close()
        self.assertFalse(pool.join(deadline=time.monotonic() + 0.03))
        release.set()
        self.assertTrue(pool.join())


if __name__ == "__main__":
    unittest.main()
