from __future__ import annotations

from collections.abc import Iterator, MutableMapping
from datetime import UTC

from bson.codec_options import CodecOptions
from fishtest.schemas import kvstore_schema
from pymongo import MongoClient
from vtjson import validate


class KeyValueStore(MutableMapping[str, object]):
    def __init__(
        self,
        db: object | None = None,
        db_name: str | None = None,
        collection: str = "kvstore",
    ) -> None:
        self.conn = None
        if db is None:
            if db_name is None:
                raise ValueError("You must specify a db or a db name")
            self.conn = MongoClient("localhost")
            codec_options = CodecOptions(tz_aware=True, tzinfo=UTC)
            db = self.conn[db_name].with_options(codec_options=codec_options)
        self.__kvstore = db[collection]

    def __setitem__(self, key: str, value: object) -> None:
        document = {"_id": key, "value": value}
        validate(kvstore_schema, document)
        self.__kvstore.replace_one({"_id": key}, document, upsert=True)

    def __getitem__(self, key: str) -> object:
        document = self.__kvstore.find_one({"_id": key})
        if document is None:
            raise KeyError(key)
        else:
            return document["value"]

    def __delitem__(self, key: str) -> None:
        d = self.__kvstore.delete_one({"_id": key})
        if d.deleted_count == 0:
            raise KeyError(key)

    def __len__(self) -> int:
        return self.__kvstore.count_documents({})

    def __iter__(self) -> Iterator[str]:
        documents = self.__kvstore.find({}, {"value": 0, "_id": 1})
        for d in documents:
            yield d["_id"]

    def values(self) -> Iterator[object]:
        documents = self.__kvstore.find({}, {"value": 1, "_id": 0})
        for d in documents:
            yield d["value"]

    def items(self) -> Iterator[tuple[str, object]]:
        documents = self.__kvstore.find()
        for d in documents:
            yield d["_id"], d["value"]

    def clear(self) -> None:
        self.__kvstore.delete_many({})

    def close(self) -> None:
        """Close the db connection if we own it
        but keep the underlying collection"""
        if self.conn is not None:
            self.conn.close()
            self.conn = None

    def drop(self) -> None:
        """Destructor!"""
        self.__kvstore.drop()
        self.close()
