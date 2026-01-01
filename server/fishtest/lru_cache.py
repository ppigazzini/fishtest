import math
import threading
import time
from collections import OrderedDict
from collections.abc import MutableMapping


class LRUCache(MutableMapping):
    __slots__ = ("__size", "__expiration", "__data", "__lock")

    def __init__(self, size=None, expiration=None):
        self.__data = OrderedDict()  # key -> (value, atime_ns)
        # Exported via .lock for callers that want to serialize multi-step access.
        self.__lock = threading.RLock()

        # Route through setters for validation and to keep purge behavior consistent.
        self.__size = None
        self.__expiration = None
        self.size = size
        self.expiration = expiration

    def __now_ns(self):
        return time.monotonic_ns()

    def __cutoff_ns(self, now_ns):
        # expiration is in seconds
        return now_ns - int(self.__expiration * 1_000_000_000)

    @staticmethod
    def __is_number(x):
        return (
            isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)
        )

    def __getitem__(self, key):
        with self.__lock:
            value, atime_ns = self.__data[key]

            if self.__expiration is not None:
                if self.__expiration <= 0:
                    del self.__data[key]
                    raise KeyError(key)
                now_ns = self.__now_ns()
                if atime_ns < self.__cutoff_ns(now_ns):
                    del self.__data[key]
                    raise KeyError(key)
            else:
                now_ns = self.__now_ns()

            # LRU touch + sliding expiration
            self.__data.move_to_end(key)
            self.__data[key] = (value, now_ns)
            return value

    def __setitem__(self, key, value):
        with self.__lock:
            self.__data[key] = (value, self.__now_ns())
            self.__data.move_to_end(key)
            self.__purge_locked()

    def __delitem__(self, key):
        with self.__lock:
            del self.__data[key]

    def __len__(self):
        with self.__lock:
            self.__purge_locked()
            return len(self.__data)

    # the default implementation is very inefficient
    def clear(self):
        with self.__lock:
            self.__data.clear()

    # the default implementation modifies the access time
    # which is semantically incorrect
    def __contains__(self, key):
        with self.__lock:
            if key not in self.__data:
                return False

            if self.__expiration is None:
                return True
            if self.__expiration <= 0:
                del self.__data[key]
                return False

            _, atime_ns = self.__data[key]
            if atime_ns < self.__cutoff_ns(self.__now_ns()):
                del self.__data[key]
                return False
            return True

    # protect iteration using self.lock if the object may be modified
    # in a different thread
    def __iter__(self):
        with self.__lock:
            self.__purge_locked()
            keys_snapshot = list(self.__data.keys())
        return iter(keys_snapshot)

    # we cannot use the default implementation of values() and items()
    # since these modify self.__dict during iteration (via the
    # calls to __getitem__)

    # protect iteration using self.lock if the object may be modified
    # in a different thread
    def values(self):
        with self.__lock:
            self.__purge_locked()
            values_snapshot = [v_atime[0] for v_atime in self.__data.values()]
        return iter(values_snapshot)

    # protect iteration using self.lock if the object may be modified
    # in a different thread
    def items(self):
        with self.__lock:
            self.__purge_locked()
            items_snapshot = [(k, v_atime[0]) for k, v_atime in self.__data.items()]
        return iter(items_snapshot)

    def purge(self):
        with self.__lock:
            self.__purge_locked()

    def __purge_locked(self):
        if self.__size is not None:
            while len(self.__data) > self.__size:
                self.__data.popitem(last=False)

        if self.__expiration is None:
            return

        if self.__expiration <= 0:
            self.__data.clear()
            return

        cutoff_ns = self.__cutoff_ns(self.__now_ns())
        expired = []
        for k, (_, atime_ns) in self.__data.items():
            if atime_ns < cutoff_ns:
                expired.append(k)
                continue
            break
        for k in expired:
            del self.__data[k]

    @property
    def size(self):
        return self.__size

    @size.setter
    def size(self, val):
        if val is not None and (
            not isinstance(val, int) or isinstance(val, bool) or val < 0
        ):
            raise ValueError("size must be None or a non-negative int")
        with self.__lock:
            self.__size = val
            self.__purge_locked()

    @property
    def expiration(self):
        return self.__expiration

    @expiration.setter
    def expiration(self, val):
        if val is not None and not self.__is_number(val):
            raise ValueError("expiration must be None or a finite number (seconds)")
        with self.__lock:
            self.__expiration = val
            self.__purge_locked()

    @property
    def lock(self):
        return self.__lock
