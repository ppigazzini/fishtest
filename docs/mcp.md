# MCP Endpoint Reference

The MCP endpoint serves fishtest's public run data, the workers playing the
active runs and the event log as read-only tools to Model Context Protocol
clients. It serves the data the read-only endpoints of
[Worker API reference](api-reference.md) and the machines page serve, each
tool with a JSON Schema contract that every call and every result is checked
against.

## Enabling

An instance serves the endpoint where `MCP_PATH` names it, such as `/mcp`, and
nowhere when `MCP_PATH` is unset. It answers a request whose `Host` is a local
address (`localhost`, `127.0.0.1`, `[::1]`, any port) or the host of
`FISHTEST_URL`; a request from a browser must come from one of those origins.

```bash
cd server
FISHTEST_INSECURE_DEV=1 MCP_PATH=/mcp uv run uvicorn fishtest.app:app --port 8000
```

## Protocol

| Property | Value |
|----------|-------|
| Transport | Streamable HTTP, stateless, answers in JSON |
| Protocol revisions | `2026-07-28`, and the earlier revisions a client opens with the `initialize` handshake |
| Methods | `POST` only; no event stream, no session |
| Capabilities | tools |
| Server name | `fishtest`; its version is a digest of the tool listing and changes with any tool's definition |

## Discovery

The server card sits beside the endpoint, at `MCP_PATH` followed by
`/server-card` (`/mcp/server-card`), where MCP reserves it. A `GET` answers
the card as `application/mcp-server-card+json`: the server's name, in reverse
DNS of the host of `FISHTEST_URL` (`org.stockfishchess.tests/fishtest`), its
title, description and version, the source repository, and the endpoint's URL
with the protocol revisions it serves. It lists no tool; a client reads them
from `tools/list`. Any page may read it, it may be cached for an hour, and a
request naming its `ETag` in `If-None-Match` is answered `304`. Without
`FISHTEST_URL` the card names no endpoint.

## Tools

Every tool reads and changes nothing (`readOnlyHint`, `idempotentHint`,
`openWorldHint: false`). `tools/list` publishes each tool's `inputSchema` and
`outputSchema`.

| Tool | Arguments | Returns |
|------|-----------|---------|
| `get_run` | `run_id` (24 hex digits) | the run's summary |
| `list_active_runs` | optionally `page` (from 1, default 1), `username` | `page`, `total`, `runs`: 50 summaries of unfinished runs a page, newest first |
| `list_finished_runs` | optionally `page` (from 1, default 1), `username`, `text`, `success_only`, `yellow_only`, `ltc_only`, `since`, `until`, `arch`, `option` | `page`, `total`, `runs`: 50 summaries a page, newest first |
| `list_active_workers` | optionally `page` (from 1, default 1), `username`, `arch`, `run_id` | `page`, `total`, `cores`, `workers`: 100 workers a page, the most recently updated first |
| `get_elo` | `run_id` | a test run's Elo estimate with its 95% interval and likelihood of superiority, as its page shows them; an SPRT test's also with its bounds and log-likelihood ratio |
| `search_actions` | optionally `run_id`, `username`, `action`, `before`, `filter`, `text` and `limit` (1 to 200, default 50) | `actions`: the newest events every argument selects |

A run summary holds the test the run asks for (`username`, `new_tag`,
`base_tag`, `tc`, `new_tc`, `threads`, `book`, `num_games`, `info`,
`tests_repo`, the resolved commits, and `new_options` and `base_options`, the
UCI options each engine starts with, such as `Hash=16 nodestime=600`), the
workers it admits where the run restricts them (`arch_filter`, the regular
expression searched in a worker's architecture; `arches`, each supported
architecture the filter admits, searched as the scheduler searches a worker's;
and `compiler`, the compiler a worker must build with), how workers take it
(`priority` and `throughput`), its state (`start_time`, `last_updated`,
`approved`, `finished`, `deleted`, `failed`, `is_green`, `is_yellow`,
`workers`, `cores`), its `results` with the pentanomial, and its `sprt`
verdict or `spsa` progress. It holds no task and no worker address. A time is
in UTC, written as `2026-11-20 20:48:11.323292+00:00`, the microseconds left
out where they are 0. A field older runs store in an earlier shape, such as a
count as a string of digits, is published as what it means; a value that
means nothing, such as a negative worker count, is left out.

`get_elo` estimates a run as its page does. An SPRT test's estimate is in the
test's Elo model, such as `normalized`, beside its bounds `elo0` and `elo1`,
its log-likelihood ratio `LLR` and the `lower_bound` and `upper_bound` at
which it is rejected and accepted. A fixed-games test's is logistic Elo, from
the pentanomial where the run holds one and from its wins, draws and losses
where it does not. `LOS` is the likelihood of superiority, from 0 to 1.

A worker plays the first run it can of the highest `priority`, so a run of a
lower priority gets a worker only where no run above it can use one: 0 is
normal, and a run below 0 is held back. Among the runs of one priority,
`throughput` is the share of cores a run asks for, as a percentage of a normal
run's at the same time control.

`list_active_workers` lists the workers the machines page lists: one row for
each task of an active run a worker is playing. A row holds the worker's
`name`, as the machines page links it (its owner's username, its cores and
the first group of its key), its `username`, `country_code` (two letters, or
`?`), `cores`, `nps`, `max_memory` in MiB, `system`, `arch` (a supported
architecture, or `unknown`), `compiler` and `compiler_version`,
`python_version`, its `version` and whether it is `modified`, and the
`run_id`, `task_id` and `last_updated` of the task it plays. It holds no
address and no more of the key than the name holds. `total` and `cores` count
every worker the filters select, on any page. The rows are read as the
machines page reads them, so a worker shows a few seconds after it takes a
task.

`search_actions` selects the events of a run (`run_id`), of an author
(`username`), of a kind (`action`) and older than a time (`before`, in seconds
since the Unix epoch), each read from an index, and those its `filter` matches.
Every argument given selects, so `run_id` alone reads a run's events, newest
first, and the last event's `time` passed as `before` reads the ones older
than it; an event that shares that exact time is left out. The filter is the
language of `POST /api/actions`: the fields, comparisons, boolean operators and
sizes its table lists, stated once under the input schema's `$defs`. The
server meets the filter and the other arguments into one query in that
language.

Each event holds its `_id`, `action`, `username` and `time`, and, where its
kind sets them, the `worker`, `run_id`, `run`, `task_id`, `message`, `nn` and
`user` the filter fields name. The record is closed and the server builds it
from those fields alone, so a field a stored document may carry beyond them
never leaves through the tool. An older event may hold a kind the server no
longer writes, such as `update_stats`, and is published as stored.

Each argument and each filter field carries a `description` with what it holds,
in its unit and case. A string in a filter matches whole and case-sensitively,
an event's `time` is in seconds since the Unix epoch, and an event's `action`
is one of the kinds the description lists; a constant in another case, unit or
type matches nothing and is not refused. `success_only` selects the green runs,
`yellow_only` the yellow ones, and `ltc_only` the runs whose base time control
is at least 40 seconds.

`since` and `until` hold `list_finished_runs` to a window on `last_updated`:
the runs last updated at or after `since` and before `until`, each a UTC date,
such as `2026-01-06`, or a UTC time in the form a summary writes, a `T` for the
space and a `Z` for `+00:00` accepted, so a summary's `last_updated` passed as
`until` pages back from that run. The window narrows the index range a listing
reads, so it adds no scan. A date the calendar does not hold, such as
`2026-02-30`, is a tool error.

`arch` holds `list_finished_runs` to the runs whose arch filter admits that
architecture: the runs whose `arches` holds it. A run that sets no arch filter
admits every architecture and is left out. No index holds the filters, so the
listing reads every run its other filters select: a window bounds what it
reads, a month of runs reading within the time bound. A listing that reads
past its time bound is a tool error that names a `since` a month back to
retry with, and the `until` that reads the month before it.

`option` holds `list_finished_runs` to the runs whose `new_options` or
`base_options` set that UCI option: `nodestime` finds the runs that play by
nodes rather than by clock. The name is matched whole and whatever its case,
as UCI reads option names, and holds no space and no `=`. No index holds the
options either, so a window bounds what the listing reads, as for `arch`.

A `text` argument searches as the search box of the matching page searches:
`list_finished_runs` the runs' `info`, as the finished-runs page does, and
`search_actions` an event's kind, author, worker, message, run name, user and
net, as the event log page does. A match holds any of the words, whatever
their case; a phrase in double quotes, typographic quotes included, is matched
as written, and a word after a minus excludes what holds it.

## Limits

| Limit | Value | Past it |
|-------|-------|---------|
| Request body | 64 KiB | HTTP `413` |
| Calls an instance reads at once | 8 | a tool error asking to retry |
| Time MongoDB spends on each read of `search_actions` and `list_finished_runs` | 2000 ms | a tool error naming what narrows the read |
| A text search | 200 characters | a tool error |
| A string in a result | 5120 characters | cut, ending in `...` |
| Caching of `tools/list` and `server/discover` | one hour, public | |

## Errors

| Situation | Answer |
|-----------|--------|
| `Host` not served | HTTP `421` |
| `Origin` present and not served | HTTP `403` |
| A method other than `POST` | HTTP `405`, `Allow: POST` |
| A tool the server does not list | JSON-RPC error `-32602`, "Unknown tool: NAME" (HTTP `400` under `2026-07-28`) |
| Arguments outside the input schema | a tool error naming where they fail, what the input schema admits there in its own terms (an unexpected key names the keys the tool takes; a value nested past the depth the server reads, such as a filter of 95 levels, asks for fewer levels), and the code; a path longer than 12 steps names its first and last 6 |
| A run the database does not hold, or `get_elo` on an SPSA tune, on a fixed-games test that has played no game, or on an SPRT test whose bounds the estimator cannot read | a tool error |
| A result outside the output schema | a tool error, "TOOL could not answer within its contract" |
| A failure in the server | JSON-RPC error `-32603`, "Internal server error", in either protocol era; the server logs the failure |

A tool error is a result with `isError: true` and the reason as text, which a
client hands back to its model.

## User-written text

A run's `info`, `new_tag`, `base_tag`, `tests_repo`, `arch_filter`,
`new_options` and `base_options`, a `username`, a worker's `name` and
`system`, and an event's `message` are written by fishtest users. Their
output schemas say so, and the server's instructions tell a client that such
text is data to report, never instructions to follow. Every string in a result
is cleared of control characters and invisible format characters, such as
bidirectional overrides; tabs, line feeds and the zero-width joiners stay. An
agent that also calls tools that write should treat these fields as untrusted
sources.

## Example

List the newest run submission on an instance started as above:

```bash
curl -s http://localhost:8000/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -H 'MCP-Protocol-Version: 2026-07-28' \
  -H 'Mcp-Method: tools/call' \
  -H 'Mcp-Name: search_actions' \
  -d '{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "search_actions",
        "arguments": {"filter": {"action": "new_run"}, "limit": 1},
        "_meta": {
          "io.modelcontextprotocol/protocolVersion": "2026-07-28",
          "io.modelcontextprotocol/clientInfo": {"name": "curl", "version": "8"},
          "io.modelcontextprotocol/clientCapabilities": {}}}}'
```

The result carries the events as `structuredContent` and as JSON text in
`content`.

## Related

- [Worker API reference](api-reference.md): the read-only HTTP endpoints and
  the `POST /api/actions` filter language.
- [Threading model](threading-model.md): the tool-call cap.
- [Deployment](deployment.md): `MCP_PATH`, `FISHTEST_URL`, and the
  stored document audit with `--mcp`.
