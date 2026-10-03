# coding=utf8
"""Small process-wide lock with no logger side effects on contention."""

import os
import sys


class InstanceLock:
    def __init__(self, path):
        self.path = os.path.abspath(path)
        self._handle = None
        self._locked = False

    def acquire(self):
        parent = os.path.dirname(self.path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        handle = open(self.path, "a+b")
        handle.seek(0)
        try:
            if sys.platform.startswith("win"):
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, IOError):
            handle.close()
            return False
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()).encode("ascii"))
        handle.flush()
        self._handle = handle
        self._locked = True
        return True

    def release(self):
        if not self._handle:
            return
        try:
            if sys.platform.startswith("win"):
                import msvcrt
                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None
            self._locked = False

    def __enter__(self):
        if not self.acquire():
            raise RuntimeError("batch instance already running")
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.release()
