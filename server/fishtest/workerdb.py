from __future__ import annotations

from datetime import UTC, datetime

from fishtest.schemas import worker_schema
from vtjson import validate

type WorkerDoc = dict[str, object]


class WorkerDb:
    def __init__(self, db: object) -> None:
        self.db: object = db
        self.workers = self.db["workers"]

    def get_worker(
        self,
        worker_name: str,
    ) -> WorkerDoc:
        q = {"worker_name": worker_name}
        r = self.workers.find_one(
            q,
        )
        if r is None:
            return {
                "worker_name": worker_name,
                "blocked": False,
                "message": "",
                "last_updated": None,
            }
        else:
            return r

    def update_worker(
        self, worker_name: str, blocked: bool | None = None, message: str | None = None
    ) -> None:
        r = {
            "worker_name": worker_name,
            "blocked": blocked,
            "message": message,
            "last_updated": datetime.now(UTC),
        }
        validate(worker_schema, r, "worker")  # may throw exception
        self.workers.replace_one({"worker_name": worker_name}, r, upsert=True)

    def get_blocked_workers(self) -> list[WorkerDoc]:
        q = {"blocked": True}
        return list(self.workers.find(q))
