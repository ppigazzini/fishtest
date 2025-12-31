import threading
import time
from collections import OrderedDict
from collections.abc import MutableMapping


class LRUCache(MutableMapping):
    def __init__(self, size=None, expiration=None):
        self.__size = size
        self.__expiration = expiration
        self.__dict = OrderedDict()

        # All methods that modify the internal state of the
        #  object (__getitem__, __setitem__, __delitem__,
        # __len__, clear, __contains__, purge and the setters
        # for size and expiration) are protected by this lock.
        # In addition the lock is exported as a property
        # so that it can be used to protect iteration over
        # the object to avoid runtime exceptions caused by the
        # object being modified in a different thread.
        self.__lock = threading.RLock()

    def __getitem__(self, key):
        with self.__lock:
            current_time = time.time()
            v, atime = self.__dict[key]
            if (
                self.__expiration is not None
                and atime < current_time - self.__expiration
            ):
                del self.__dict[key]
                raise KeyError(key)
            self.__dict.move_to_end(key)
            self.__dict[key] = (v, current_time)
            return v

    def __setitem__(self, key, value):
        with self.__lock:
            self.__dict[key] = (value, time.time())
            self.__dict.move_to_end(key)
            self.__purge()

    def __delitem__(self, key):
        with self.__lock:
            del self.__dict[key]

    def __len__(self):
        with self.__lock:
            self.__purge()
            return len(self.__dict)

    # the default implementation is very inefficient
    def clear(self):
        with self.__lock:
            self.__dict.clear()

    # the default implementation modifies the access time
    # which is semantically incorrect
    def __contains__(self, key):
        with self.__lock:
            if key not in self.__dict:
                return False
            current_time = time.time()
            v, atime = self.__dict[key]
            if (
                self.__expiration is not None
                and atime < current_time - self.__expiration
            ):
                del self.__dict[key]
                return False
            return True

    # protect iteration using self.lock if the object may be modified
    # in a different thread
    def __iter__(self):
        self.__purge()
        return iter(self.__dict)

    # we cannot use the default implementation of values() and items()
    # since these modify self.__dict during iteration (via the
    # calls to __getitem__)

    # protect iteration using self.lock if the object may be modified
    # in a different thread
    def values(self):
        self.purge()
        for v in self.__dict.values():
            yield v[0]

    # protect iteration using self.lock if the object may be modified
    # in a different thread
    def items(self):
        self.purge()
        for k, v in self.__dict.items():
            yield k, v[0]

    def purge(self):
        with self.__lock:
            self.__purge()

    def __purge(self):
        if self.__size is not None:
            while len(self.__dict) > self.__size:
                self.__dict.popitem(last=False)
        if self.__expiration is not None:
            expired = []
            cutoff_time = time.time() - self.__expiration
            for k, v in self.__dict.items():
                atime = v[1]
                if atime < cutoff_time:
                    expired.append(k)
                    continue
                break
            for k in expired:
                del self.__dict[k]

    @property
    def size(self):
        return self.__size

    @size.setter
    def size(self, val):
        with self.__lock:
            self.__size = val
            self.__purge()

    @property
    def expiration(self):
        return self.__expiration

    @expiration.setter
    def expiration(self, val):
        with self.__lock:
            self.__expiration = val
            self.__purge()

    @property
    def lock(self):
        return self.__lock
