from __future__ import annotations

"""Lightweight shared type aliases for Fishtest server.

These aliases intentionally avoid importing from `typing` unless required.
They describe runtime DB documents and API payloads which may contain BSON types,
datetimes, and other non-JSON values.
"""

# Generic document/value shapes used throughout the server.

type DbValue = object

type DbDoc = dict[str, DbValue]

type DbDocId = str

type DbDocList = list[DbDoc]

# JSON-ish payloads (note: may still contain non-JSON runtime values).

type JsonValue = object

type JsonDict = dict[str, JsonValue]

type JsonList = list[JsonValue]

# Common domain aliases.

type RunId = str

type TaskId = int

type Username = str

type WorkerName = str

# This is "worker_info" as stored in tasks and received from workers.
# It is intentionally loose because it carries many dynamic fields.

type WorkerInfo = DbDoc

# Task and Run records are MongoDB documents.

type TaskDoc = DbDoc

type RunDoc = DbDoc

type StatsDoc = DbDoc
