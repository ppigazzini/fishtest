"""How the MCP server is served over HTTP: its route and its server card.

`streamable_http_route` serves a server from `fishtest.mcp.server` at
`MCP_PATH` over Streamable HTTP, answering only the hosts and origins
`transport_security` names, and `server_card_route` serves the card a client
discovers the endpoint by. `app.py` mounts both.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from urllib.parse import urlsplit

from mcp.server.lowlevel import Server
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types.version import SUPPORTED_PROTOCOL_VERSIONS
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from fishtest.api import ACTIONS_BODY_MAX
from fishtest.mcp.contracts import TOOLS_DIGEST

# One line on what the server does: the description of the server card.
DESCRIPTION = "Read-only fishtest test runs, results, SPRT verdicts and event log."


# A local address on any port: what a development instance is reached at.
LOCAL_HOSTS = ("127.0.0.1", "localhost", "[::1]")


def transport_security(public_url: str | None) -> TransportSecuritySettings:
    """Return the hosts and origins the endpoint serves.

    A local host, and the host of `public_url` when one is set. A request
    naming another host is refused 421, which stops a page that rebinds its
    own name to a local address; a request from another origin is refused 403.
    A request without an `Origin`, which is every client but a browser, is
    served.
    """
    hosts = [host for local in LOCAL_HOSTS for host in (local, f"{local}:*")]
    origins = [f"http://{host}" for host in hosts]
    if public_url is not None:
        parts = urlsplit(public_url)
        hosts.append(parts.netloc)
        origins.append(f"{parts.scheme}://{parts.netloc}")
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
        allowed_origins=origins,
    )


def streamable_http_route(
    server: Server[Any], path: str, public_url: str | None
) -> Route:
    """Return the route that serves `server` at `path` over Streamable HTTP.

    Stateless and answering in JSON, so any instance behind the proxy answers
    any request and no session lives in one process. Each request's `Host` and
    `Origin` are checked against `transport_security(public_url)`, and a body
    is held to the size `/api/actions` reads, the largest any tool needs; a
    larger one is answered 413. The route takes POST alone: a stateless server
    sends nothing a client did not ask for, so it opens no event stream for a
    GET and ends no session for a DELETE, and either is answered 405. The
    app's lifespan runs `server.session_manager`.
    """
    app = server.streamable_http_app(
        streamable_http_path=path,
        stateless_http=True,
        json_response=True,
        max_request_body_size=ACTIONS_BODY_MAX,
        transport_security=transport_security(public_url),
    )
    (route,) = app.routes
    if not isinstance(route, Route):
        msg = f"the SDK serves {path} through {type(route).__name__}, not a Route"
        raise TypeError(msg)
    return Route(path, endpoint=route.endpoint, methods=["POST"])


# The server card's schema and media type, and the headers it is served with:
# public metadata any page may read, cached an hour and revalidated by ETag.
SERVER_CARD_SCHEMA = (
    "https://static.modelcontextprotocol.io/schemas/v1/server-card.schema.json"
)
SERVER_CARD_TYPE = "application/mcp-server-card+json"
SERVER_CARD_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET",
    "Access-Control-Allow-Headers": "Content-Type, If-None-Match",
    "Access-Control-Expose-Headers": "ETag",
    "Cache-Control": "public, max-age=3600",
}


def server_card(path: str, public_url: str | None) -> dict[str, Any]:
    """Return the server card: who the server is and where a client connects.

    The card names the listing's digest as its version, as `serverInfo` does,
    and lists no tool: tools are read from `tools/list`. Its one remote is the
    endpoint at `public_url`, and a card without a public URL names none.
    """
    host = urlsplit(public_url).hostname if public_url is not None else None
    namespace = ".".join(reversed(host.split("."))) if host else "localhost"
    card: dict[str, Any] = {
        "$schema": SERVER_CARD_SCHEMA,
        "name": f"{namespace}/fishtest",
        "title": "Fishtest",
        "description": DESCRIPTION,
        "version": TOOLS_DIGEST,
        "repository": {
            "url": "https://github.com/official-stockfish/fishtest",
            "source": "github",
        },
    }
    if public_url is not None:
        card["websiteUrl"] = public_url
        card["remotes"] = [
            {
                "type": "streamable-http",
                "url": f"{public_url}{path}",
                "supportedProtocolVersions": list(SUPPORTED_PROTOCOL_VERSIONS),
            }
        ]
    return card


def server_card_route(path: str, public_url: str | None) -> Route:
    """Return the route that serves the server card at `<path>/server-card`.

    The location MCP reserves for a card beside a Streamable HTTP endpoint. A
    request naming the card's ETag in If-None-Match is answered 304.
    """
    body = json.dumps(server_card(path, public_url), sort_keys=True).encode()
    etag = f'"{hashlib.sha256(body).hexdigest()[:16]}"'
    headers = {**SERVER_CARD_HEADERS, "ETag": etag}

    async def card(request: Request) -> Response:
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        return Response(body, media_type=SERVER_CARD_TYPE, headers=headers)

    return Route(f"{path}/server-card", endpoint=card, methods=["GET"])
