#!/usr/bin/env python3
"""Call every MCP tool of a running instance, in each protocol era, and report.

The smoke test only reads. It connects to the MCP endpoint at a URL once per
protocol era, 2026-07-28 and the 2025-11-25 handshake, and checks that the
instance serves this checkout's listing, that each tool answers a call made
from the instance's own runs, that a call outside a contract is a tool error,
and that a tool the server does not list is a JSON-RPC error. It prints one
line per check and exits 1 when one fails; a check that has no run to read is
skipped.

    cd server
    uv run python utils/mcp_smoke.py https://SERVER_NAME/mcp
"""

import argparse
import asyncio
import sys

from mcp import Client
from mcp.shared.exceptions import MCPError
from mcp.types import INVALID_PARAMS, TextContent

from fishtest.mcp.contracts import TOOLS, TOOLS_DIGEST

ERAS = ("2026-07-28", "legacy")


async def smoke(target, mode):
    """Return the checks run against `target` in `mode`: (name, outcome, detail).

    The outcome is True or False, or None for a check skipped.
    """
    checks = []

    def check(name, passed, detail=""):
        checks.append((name, None if passed is None else bool(passed), detail))

    async with Client(target, mode=mode) as client:
        listing = await client.list_tools()
        names = [tool.name for tool in listing.tools]
        check("tools/list", names == [tool["name"] for tool in TOOLS], " ".join(names))
        info = (listing.meta or {}).get("io.modelcontextprotocol/serverInfo", {})
        version = info.get("version") or getattr(client.server_info, "version", "")
        check("version", version == TOOLS_DIGEST, f"{version}, checkout {TOOLS_DIGEST}")

        runs = []
        for name, arguments in (
            ("list_active_runs", {}),
            ("list_finished_runs", {"page": 1}),
        ):
            result = await client.call_tool(name, arguments)
            found = (result.structured_content or {}).get("runs", [])
            check(name, not result.is_error, f"{len(found)} runs")
            runs += found

        if runs:
            run_id = runs[0]["run_id"]
            result = await client.call_tool("get_run", {"run_id": run_id})
            check("get_run", not result.is_error, run_id)
        else:
            check("get_run", None, "no run to read")
        sprt = [run["run_id"] for run in runs if "sprt" in run]
        if sprt:
            result = await client.call_tool("get_elo", {"run_id": sprt[0]})
            check("get_elo", not result.is_error, sprt[0])
        else:
            check("get_elo", None, "no SPRT run to read")

        query = {"filter": {"action": "new_run"}, "limit": 1}
        result = await client.call_tool("search_actions", query)
        found = (result.structured_content or {}).get("actions", [])
        check("search_actions", not result.is_error, f"{len(found)} actions")

        result = await client.call_tool("get_run", {"run_id": "not a run"})
        text = " ".join(c.text for c in result.content if isinstance(c, TextContent))
        check("a refused call", result.is_error, text)

        try:
            await client.call_tool("no_such_tool", {})
        except MCPError as e:
            check("an unknown tool", e.code == INVALID_PARAMS, f"{e.code} {e.message}")
        else:
            check("an unknown tool", False, "answered as a result")
    return checks


async def run(url):
    passed = True
    for mode in ERAS:
        for name, outcome, detail in await smoke(url, mode):
            label = {True: "ok", False: "FAIL", None: "skip"}[outcome]
            print(f"{mode:<10}  {label:<4}  {name}: {detail}")
            passed &= outcome is not False
    return passed


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("url", help="the MCP endpoint, such as https://host/mcp")
    args = parser.parse_args()
    sys.exit(0 if asyncio.run(run(args.url)) else 1)


if __name__ == "__main__":
    main()
