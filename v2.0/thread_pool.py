# coding=utf8
"""Small cooperative thread pool used by the legacy crawlers."""

import contextlib
import queue
import threading
import time


StopEvent = object()


def callback(status, result):
    """Backward-compatible no-op callback."""
    return None


def action(thread_name, arg):
    time.sleep(0.1)
    print("第%s个任务调用了线程 %s，并打印了这条信息！" % (arg + 1, thread_name))


class ThreadPool:
    def __init__(self, max_num, max_task_num=None):
        self.max_num = max(1, int(max_num))
        if max_task_num is not None and max_task_num < 0:
            raise ValueError("max_task_num must be non-negative")
        self.q = queue.Queue(maxsize=max_task_num or 0)
        self.cancel = False
        self.terminal = False
        self._closed = False
        self._lock = threading.RLock()
        self._threads = []
        self._active = 0
        self._completed = 0
        self._cancelled = 0
        self._errors = []
        # Kept for callers that inspect the old pool state.
        self.generate_list = []
        self.free_list = []

    @property
    def errors(self):
        with self._lock:
            return list(self._errors)

    @property
    def stats(self):
        with self._lock:
            return {
                "completed": self._completed,
                "cancelled": self._cancelled,
                "active": self._active,
                "queued": self.q.qsize(),
                "errors": len(self._errors),
            }

    def _ensure_worker(self):
        with self._lock:
            live = [thread for thread in self._threads if thread.is_alive()]
            self._threads = live
            if len(live) >= self.max_num:
                return
            thread = threading.Thread(target=self.call)
            thread.daemon = False
            self._threads.append(thread)
            self.generate_list.append(thread.name)
            thread.start()

    def put(self, func, args, callback=None, stop_event=None, deadline=None):
        """Queue one task, returning ``False`` when it was not accepted.

        The first three arguments preserve the crawler API.  The optional
        cooperative controls only affect this submission and do not require
        changing legacy task callables.
        """
        with self._lock:
            if self.cancel or self._closed:
                self._cancelled += 1
                return False
        if stop_event is not None and stop_event.is_set():
            with self._lock:
                self._cancelled += 1
            return False
        if deadline is not None and time.monotonic() >= deadline:
            with self._lock:
                self._cancelled += 1
            return False
        self._ensure_worker()
        item = (func, tuple(args or ()), callback, stop_event, deadline)
        while True:
            if stop_event is not None and stop_event.is_set():
                with self._lock:
                    self._cancelled += 1
                return False
            if deadline is not None and time.monotonic() >= deadline:
                with self._lock:
                    self._cancelled += 1
                return False
            try:
                self.q.put(item, timeout=0.1)
                return True
            except queue.Full:
                with self._lock:
                    if self.cancel or self._closed:
                        self._cancelled += 1
                        return False

    def generate_thread(self):
        self._ensure_worker()

    def _record_error(self, kind, exc):
        with self._lock:
            self._errors.append((kind, exc))

    def call(self):
        current = threading.current_thread().name
        with self.worker_state(self.free_list, current):
            while True:
                try:
                    event = self.q.get(timeout=0.1)
                except queue.Empty:
                    with self._lock:
                        should_exit = self._closed or self.terminal
                    if should_exit:
                        break
                    continue
                if event is StopEvent:
                    self.q.task_done()
                    break
                func, arguments, cb, stop_event, deadline = event
                with self._lock:
                    self._active += 1
                success = False
                result = None
                try:
                    cancelled = self.terminal or (stop_event is not None and stop_event.is_set())
                    if deadline is not None and time.monotonic() >= deadline:
                        cancelled = True
                    if cancelled:
                        with self._lock:
                            self._cancelled += 1
                    else:
                        result = func(*arguments)
                        success = True
                except Exception as exc:
                    self._record_error("task", exc)
                finally:
                    if cb is not None:
                        try:
                            cb(success, result)
                        except Exception as exc:
                            self._record_error("callback", exc)
                    with self._lock:
                        self._active -= 1
                        self._completed += 1
                    self.q.task_done()
            with self._lock:
                if current in self.generate_list:
                    self.generate_list.remove(current)

    def close(self):
        """Stop accepting new work and let queued work drain.

        Closing is deliberately non-blocking for compatibility with the
        original crawler.  Callers that need a phase boundary must call
        :meth:`join` afterwards.
        """
        with self._lock:
            self.cancel = True
            self._closed = True

    def terminate(self):
        """Cooperatively cancel queued work and stop workers after active work."""
        with self._lock:
            self.terminal = True
            self.cancel = True
            self._closed = True
        self._cancel_queued(invoke_callbacks=True)

    def join(self, stop_event=None, deadline=None):
        """Wait until queued work and all real worker threads have finished.

        A deadline only bounds this waiter's observation.  Returning ``False``
        never claims that workers disappeared; callers must keep the phase
        closed and must not cross a completion barrier while a thread remains.
        """
        while True:
            if stop_event is not None and stop_event.is_set():
                self.terminate()
            with self._lock:
                threads = list(self._threads)
                done = self.q.unfinished_tasks == 0 and self._active == 0
            if done and not any(thread.is_alive() for thread in threads):
                return True
            if deadline is not None and time.monotonic() >= deadline:
                # Do not claim a barrier while an uncooperative task remains.
                return False
            current = threading.current_thread()
            wait_for = 0.05
            if deadline is not None:
                wait_for = max(0.0, min(wait_for, deadline - time.monotonic()))
            for thread in threads:
                if thread is current or not thread.is_alive():
                    continue
                thread.join(timeout=wait_for)
            if not threads:
                time.sleep(wait_for)

    def _cancel_queued(self, invoke_callbacks=False):
        while True:
            try:
                event = self.q.get_nowait()
            except queue.Empty:
                return
            else:
                with self._lock:
                    self._cancelled += 1
                    self._completed += 1
                if invoke_callbacks and event is not StopEvent:
                    _, _, cb, _, _ = event
                    if cb is not None:
                        try:
                            cb(False, None)
                        except Exception as exc:
                            self._record_error("callback", exc)
                self.q.task_done()

    @contextlib.contextmanager
    def worker_state(self, state_list, worker_thread):
        with self._lock:
            state_list.append(worker_thread)
        try:
            yield
        finally:
            with self._lock:
                if worker_thread in state_list:
                    state_list.remove(worker_thread)


if __name__ == "__main__":
    pool = ThreadPool(5)
    for index in range(100):
        pool.put(action, (index,), callback)
    pool.close()
    pool.join()
