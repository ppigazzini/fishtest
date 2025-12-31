from __future__ import annotations

from datetime import UTC, datetime

from bson.objectid import ObjectId
from fishtest._types import RunDoc
from fishtest.schemas import ACTION_MESSAGE_SIZE, action_schema
from fishtest.util import hex_print, worker_name
from pymongo import DESCENDING
from vtjson import ValidationError, validate


def run_name(run: RunDoc) -> str:
    run_id = str(run["_id"])
    run = run["args"]["new_tag"]
    return run[:23] + "-" + hex_print(run_id)[0:7]


class ActionDb:
    def __init__(self, db: object) -> None:
        self.db: object = db
        self.actions = self.db["actions"]

    def get_actions(
        self,
        username: str | None = None,
        action: str | None = None,
        text: str | None = None,
        limit: int = 0,
        skip: int = 0,
        utc_before: float | None = None,
        run_id: str | None = None,
        max_actions: int | None = None,
    ) -> tuple[object, int]:
        q = {}
        if action:
            # update_stats is no longer used, but included for backward compatibility
            if action == "system_event":
                q["action"] = {"$in": ["system_event", "update_stats"]}
            else:
                q["action"] = action
        else:
            q["action"] = {"$nin": ["system_event", "update_stats", "dead_task"]}
        if username:
            q["username"] = username
        if text:
            q["$text"] = {"$search": text}
        if utc_before:
            q["time"] = {"$lte": utc_before}
        if run_id:
            q["run_id"] = str(run_id)

        if max_actions:
            count = self.actions.count_documents(q, limit=max_actions)
            limit = min(limit, max_actions - skip)
        else:
            count = self.actions.count_documents(q)

        actions_list = self.actions.find(
            q, limit=limit, skip=skip, sort=[("_id", DESCENDING)]
        )

        return actions_list, count

    def failed_task(
        self,
        username: str | None = None,
        run: RunDoc | None = None,
        task_id: int | None = None,
        message: str | None = None,
    ) -> None:
        task = run["tasks"][task_id]
        self.insert_action(
            action="failed_task",
            username=username,
            worker=worker_name(task["worker_info"]),
            run_id=run["_id"],
            run=run_name(run),
            task_id=task_id,
            message=message[:ACTION_MESSAGE_SIZE],
        )

    def crash_or_time(
        self,
        username: str | None = None,
        run: RunDoc | None = None,
        task_id: int | None = None,
        message: str | None = None,
    ) -> None:
        task = run["tasks"][task_id]
        self.insert_action(
            action="crash_or_time",
            username=username,
            worker=worker_name(task["worker_info"]),
            run_id=run["_id"],
            run=run_name(run),
            task_id=task_id,
            message=message[:ACTION_MESSAGE_SIZE],
        )

    def stop_run(
        self,
        username: str | None = None,
        run: RunDoc | None = None,
        task_id: int | None = None,
        message: str | None = None,
    ) -> None:
        if task_id is not None:
            task = run["tasks"][task_id]
            self.insert_action(
                action="stop_run",
                username=username,
                worker=worker_name(task["worker_info"]),
                run_id=run["_id"],
                run=run_name(run),
                task_id=task_id,
                message=message[:ACTION_MESSAGE_SIZE],
            )
        else:
            self.insert_action(
                action="stop_run",
                username=username,
                run_id=run["_id"],
                run=run_name(run),
                message=message[:ACTION_MESSAGE_SIZE],
            )

    def dead_task(
        self,
        username: str | None = None,
        run: RunDoc | None = None,
        task_id: int | None = None,
    ) -> None:
        task = run["tasks"][task_id]
        self.insert_action(
            action="dead_task",
            username=username,
            worker=worker_name(task["worker_info"]),
            run_id=run["_id"],
            run=run_name(run),
            task_id=task_id,
        )

    def system_event(self, message: str | None = None) -> None:
        self.insert_action(
            action="system_event",
            username="fishtest.system",
            message=message,
        )

    def new_run(
        self,
        username: str | None = None,
        run: RunDoc | None = None,
        message: str | None = None,
    ) -> None:
        self.insert_action(
            action="new_run",
            username=username,
            run_id=run["_id"],
            run=run_name(run),
            message=message,
        )

    def finished_run(
        self,
        username: str | None = None,
        run: RunDoc | None = None,
        message: str | None = None,
    ) -> None:
        self.insert_action(
            action="finished_run",
            username=username,
            run_id=run["_id"],
            run=run_name(run),
            message=message,
        )

    def upload_nn(self, username: str | None = None, nn: str | None = None) -> None:
        self.insert_action(
            action="upload_nn",
            username=username,
            nn=nn,
        )

    def modify_run(
        self,
        username: str | None = None,
        run: RunDoc | None = None,
        message: str | None = None,
    ) -> None:
        self.insert_action(
            action="modify_run",
            username=username,
            run_id=run["_id"],
            run=run_name(run),
            message=message,
        )

    def delete_run(
        self, username: str | None = None, run: RunDoc | None = None
    ) -> None:
        self.insert_action(
            action="delete_run",
            username=username,
            run_id=run["_id"],
            run=run_name(run),
        )

    def approve_run(
        self,
        username: str | None = None,
        run: RunDoc | None = None,
        message: str | None = None,
    ) -> None:
        self.insert_action(
            action="approve_run",
            username=username,
            run_id=run["_id"],
            run=run_name(run),
            message=message,
        )

    def purge_run(
        self,
        username: str | None = None,
        run: RunDoc | None = None,
        message: str | None = None,
    ) -> None:
        self.insert_action(
            action="purge_run",
            username=username,
            run_id=run["_id"],
            run=run_name(run),
            message=message,
        )

    def block_user(
        self,
        username: str | None = None,
        user: str | None = None,
        message: str | None = None,
    ) -> None:
        self.insert_action(
            action="block_user",
            username=username,
            user=user,
            message=message,
        )

    def accept_user(
        self,
        username: str | None = None,
        user: str | None = None,
        message: str | None = None,
    ) -> None:
        self.insert_action(
            action="accept_user",
            username=username,
            user=user,
            message=message,
        )

    def block_worker(
        self,
        username: str | None = None,
        worker: str | None = None,
        message: str | None = None,
    ) -> None:
        self.insert_action(
            action="block_worker",
            username=username,
            worker=worker,
            message=message,
        )

    def log_message(
        self,
        username: str | None = None,
        worker: str | None = None,
        message: str | None = None,
    ) -> None:
        if worker is None:
            self.insert_action(
                action="log_message",
                username=username,
                message=message,
            )
        else:
            self.insert_action(
                action="log_message",
                username=username,
                worker=worker,
                message=message,
            )

    def insert_action(self, **action: object) -> None:
        if "run_id" in action:
            action["run_id"] = str(action["run_id"])
        action["time"] = datetime.now(UTC).timestamp()
        action["_id"] = ObjectId()
        try:
            validate(action_schema, action, "action")
        except ValidationError as e:
            message = (
                f"Internal Error. Request {str(action)} does not validate: {str(e)}"
            )
            print(message, flush=True)
            self.log_message(
                username="fishtest.system",
                message=message,
            )
            return
        self.actions.insert_one(action)
