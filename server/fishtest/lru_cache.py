import threading
import time
from collections import OrderedDict
from collections.abc import MutableMapping


class LRUCache(MutableMapping):
    def __init__(self, size=None, expiration=None):
        self.__size = size
        self.__expiration = expiration
        self.__dict = OrderedDict()
        self.__lock = threading.Lock()

    def __getitem__(self, key):
        with self.__lock:
            current_time = time.time()
            self.__dict.move_to_end(key)
            v, atime = self.__dict[key]
            if (
                self.__expiration is not None
                and atime < current_time - self.__expiration
            ):
                raise KeyError(key)
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

    # not thread safe
    def __iter__(self):
        self.__purge()
        return iter(self.__dict)

    # we cannot use the default implementation of values() and items()
    # since these modify self.__dict during iteration (via the
    # calls to __getitem__)

    # not thread safe
    def values(self):
        self.__purge()
        for v in self.__dict.values():
            yield v[0]

    # not thread safe        
    def items(self):
        self.__purge()
        for k,v in self.__dict.items():
            yield k, v[0]

    def __purge(self):
        if self.__size is not None:
            while len(self.__dict) > self.size:
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
