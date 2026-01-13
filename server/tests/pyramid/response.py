"""Minimal `pyramid.response` stubs."""

from __future__ import annotations

import json
from collections.abc import Iterator


class Response:
    def __init__(
        self, body: bytes | str | None = None, *, json_body=None, content_type=None
    ):
        self.body = body
        self.json_body = json_body
        self.content_type = content_type
        self.status_int = 200
        self.headers = {}

    @property
    def status(self):
        # Pyramid allows setting `response.status` (commonly as int).
        return self.status_int

    @status.setter
    def status(self, value):
        if isinstance(value, int):
            self.status_int = value
            return
        if isinstance(value, str):
            # Accept strings like "404 Not Found" by parsing the leading code.
            try:
                self.status_int = int(value.split()[0])
            except Exception:
                self.status_int = 500
            return
        self.status_int = 500

    def __bytes__(self) -> bytes:
        if self.json_body is not None:
            return json.dumps(self.json_body).encode("utf-8")
        if self.body is None:
            return b""
        if isinstance(self.body, bytes):
            return self.body
        return str(self.body).encode("utf-8")


class FileIter:
    """Very small stand-in for Pyramid's FileIter."""

    def __init__(self, fileobj, block_size: int = 1 << 16):
        self._fileobj = fileobj
        self._block_size = block_size

    def __iter__(self) -> Iterator[bytes]:
        while True:
            chunk = self._fileobj.read(self._block_size)
            if not chunk:
                break
            yield chunk
