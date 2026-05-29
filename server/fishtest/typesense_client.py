"""Minimal Typesense HTTP client for the Phase 1 server-side search rollout."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import requests

if TYPE_CHECKING:
    from requests import Response, Session


_HTTP_BAD_REQUEST = 400
_HTTP_NOT_FOUND = 404
_HTTP_SERVER_ERROR = 500


@dataclass(frozen=True, slots=True)
class TypesenseClientConfig:
    """Connection settings for the Typesense HTTP API."""

    host: str
    api_key: str
    timeout_seconds: float


class TypesenseError(RuntimeError):
    """Base error for Typesense client failures."""


class TypesenseUnavailableError(TypesenseError):
    """Raised when Typesense cannot be reached or is temporarily unhealthy."""


class TypesenseApiError(TypesenseError):
    """Raised when Typesense returns a non-success API response."""


class TypesenseImportError(TypesenseApiError):
    """Raised when one or more JSONL import records fail."""

    def __init__(self, errors: list[dict[str, Any]]) -> None:
        """Store failed import records for later inspection."""
        self.errors = errors
        super().__init__(f"Typesense import failed for {len(errors)} document(s)")


class TypesenseClient:
    """Issue authenticated requests to the Typesense HTTP API."""

    def __init__(
        self,
        config: TypesenseClientConfig,
        *,
        session: Session | None = None,
    ) -> None:
        """Store connection settings and an optional shared HTTP session."""
        self._base_url = config.host.rstrip("/")
        self._timeout_seconds = config.timeout_seconds
        self._session = session or requests.Session()
        self._headers = {
            "X-TYPESENSE-API-KEY": config.api_key,
            "Accept": "application/json",
        }

    def close(self) -> None:
        """Close the underlying HTTP session."""
        self._session.close()

    def search(self, collection: str, search_params: dict[str, Any]) -> dict[str, Any]:
        """Run a search query against a collection or alias."""
        response = _require_response(
            self._request(
                "GET",
                f"/collections/{collection}/documents/search",
                params=search_params,
            ),
        )
        return self._json(response)

    def import_documents(
        self,
        collection: str,
        documents: list[dict[str, Any]],
        *,
        action: str = "upsert",
    ) -> list[dict[str, Any]]:
        """Bulk-import JSONL documents into a collection or alias."""
        payload = "\n".join(
            json.dumps(document, separators=(",", ":")) for document in documents
        )
        response = _require_response(
            self._request(
                "POST",
                f"/collections/{collection}/documents/import",
                params={"action": action},
                data=payload,
                content_type="text/plain",
            ),
        )
        lines = [
            json.loads(line) for line in response.text.splitlines() if line.strip()
        ]
        errors = [line for line in lines if not line.get("success")]
        if errors:
            raise TypesenseImportError(errors)
        return lines

    def get_collection(
        self,
        collection: str,
        *,
        allow_missing: bool = False,
    ) -> dict[str, Any] | None:
        """Return a collection schema, or None when missing is allowed."""
        response = self._request(
            "GET",
            f"/collections/{collection}",
            allow_missing=allow_missing,
        )
        if response is None:
            return None
        return self._json(response)

    def create_collection(self, schema: dict[str, Any]) -> dict[str, Any]:
        """Create a collection with a predefined schema."""
        response = _require_response(
            self._request("POST", "/collections", json_body=schema),
        )
        return self._json(response)

    def get_alias(
        self,
        alias: str,
        *,
        allow_missing: bool = False,
    ) -> dict[str, Any] | None:
        """Return an alias mapping, or None when missing is allowed."""
        response = self._request(
            "GET",
            f"/aliases/{alias}",
            allow_missing=allow_missing,
        )
        if response is None:
            return None
        return self._json(response)

    def upsert_alias(self, alias: str, collection_name: str) -> dict[str, Any]:
        """Create or update an alias to point to a collection."""
        response = _require_response(
            self._request(
                "PUT",
                f"/aliases/{alias}",
                json_body={"collection_name": collection_name},
            ),
        )
        return self._json(response)

    def _request(  # noqa: PLR0913
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        data: str | None = None,
        content_type: str = "application/json",
        allow_missing: bool = False,
    ) -> Response | None:
        headers = dict(self._headers)
        headers["Content-Type"] = content_type
        try:
            response = self._session.request(
                method,
                f"{self._base_url}{path}",
                params=params,
                json=json_body,
                data=data,
                headers=headers,
                timeout=self._timeout_seconds,
            )
        except requests.RequestException as exc:
            raise TypesenseUnavailableError(str(exc)) from exc

        if response.status_code == _HTTP_NOT_FOUND and allow_missing:
            return None
        if response.status_code >= _HTTP_SERVER_ERROR:
            message = _typesense_unavailable_message(
                method=method,
                path=path,
                status_code=response.status_code,
            )
            raise TypesenseUnavailableError(message)
        if response.status_code >= _HTTP_BAD_REQUEST:
            message = _typesense_api_message(
                method=method,
                path=path,
                status_code=response.status_code,
                response_text=response.text,
            )
            raise TypesenseApiError(message)
        return response

    @staticmethod
    def _json(response: Response) -> dict[str, Any]:
        return response.json()


def _typesense_unavailable_message(*, method: str, path: str, status_code: int) -> str:
    return f"Typesense {method} {path} failed with {status_code}"


def _typesense_api_message(
    *,
    method: str,
    path: str,
    status_code: int,
    response_text: str,
) -> str:
    return f"Typesense {method} {path} failed with {status_code}: {response_text}"


def _require_response(response: Response | None) -> Response:
    if response is None:
        msg = "Typesense returned no response for a non-optional request"
        raise TypesenseApiError(msg)
    return response


__all__ = [
    "TypesenseApiError",
    "TypesenseClient",
    "TypesenseClientConfig",
    "TypesenseError",
    "TypesenseImportError",
    "TypesenseUnavailableError",
]
