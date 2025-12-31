from __future__ import annotations

from collections import OrderedDict


class LRUCache(OrderedDict[object, object]):
    def __init__(self, size: int) -> None:
        super().__init__()
        self.__size = size

    def __setitem__(self, key: object, value: object) -> None:
        super().__setitem__(key, value)
        self.move_to_end(key)
        if len(self) > self.__size:
            self.popitem(last=False)

    def __getitem__(self, key: object) -> object:
        self.move_to_end(key)
        return super().__getitem__(key)

    @property
    def size(self) -> int:
        return self.__size

    @size.setter
    def size(self, val: int) -> None:
        while len(self) > val:
            self.popitem(last=False)
        self.__size = val
