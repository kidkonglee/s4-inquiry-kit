"""Bounded, thread-safe limits. Forwarded client headers are never trusted."""
from collections import OrderedDict, deque
import threading
import time

class RateLimiter:
    def __init__(self, max_keys=5000):
        self.entries = OrderedDict()
        self.max_keys = max_keys
        self.lock = threading.Lock()

    def allow(self, key, limit, seconds=60):
        now = time.monotonic()
        with self.lock:
            q = self.entries.pop(key, deque())
            while q and q[0] <= now - seconds:
                q.popleft()
            allowed = len(q) < limit
            if allowed:
                q.append(now)
            self.entries[key] = q
            while len(self.entries) > self.max_keys:
                self.entries.popitem(last=False)
            return allowed

    def clear(self):
        with self.lock:
            self.entries.clear()

class LimitReached(Exception):
    pass
