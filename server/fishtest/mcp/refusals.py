"""How the MCP server words a refused call and logs a refused result.

A call outside its contract is answered with the first failure's path, what
the input schema admits there in JSON Schema's terms, which the client holds,
and the failure's code. A key the input schema does not declare is written
`*`: it is text the caller chose, as valgebra-planning writes it. The words are read from the schema through
valgebra-jsonschema's readers, `Compiler.resolve`, `branches`, `constants`,
`interval` and `ANNOTATIONS`, at the argument's slot in valgebra-planning's
contract; a subschema they do not word keeps valgebra's expectation. A result
outside its contract is logged by its first failure's path and code. Neither
names the value.
"""

from __future__ import annotations

import json
from fractions import Fraction
from typing import Any

from valgebra import ValidationError
from valgebra_jsonschema import (
    ANNOTATIONS,
    Compiler,
    Interval,
    branches,
    constants,
    interval,
)
from valgebra_planning.contracts import Contract

from fishtest.mcp.contracts import CONTRACTS, TOOLS


def _followed(compiler: Compiler, node: Any) -> Any:
    # A subschema with each reference followed to the schema it names.
    while isinstance(node, dict) and "$ref" in node:
        node = compiler.resolve(node["$ref"])
    return node


def _step(compiler: Compiler, node: Any, part: str | int) -> Any:
    # The subschema one step of a failure's path enters: an array's item, or a
    # property of the union branch that declares it; None for neither.
    for branch in branches(node) or [node]:
        branch = _followed(compiler, branch)
        if not isinstance(branch, dict):
            continue
        if isinstance(part, int) and isinstance(branch.get("items"), dict):
            return _followed(compiler, branch["items"])
        if isinstance(part, str) and part in branch.get("properties", {}):
            return _followed(compiler, branch["properties"][part])
    return None


def _schema_at(
    contract: Contract, path: tuple[str | int, ...]
) -> tuple[Compiler, Any] | None:
    # The subschema a failure's path names, as the input schema writes it: the
    # argument's slot, then each step below it; None where the path leaves it.
    if not path:
        return None
    try:
        slot = contract.argument(str(path[0]))
    except KeyError:
        return None
    compiler = Compiler(slot.root)
    node = _followed(compiler, slot.schema)
    for part in path[1:]:
        node = _step(compiler, node, part)
        if node is None:
            return None
    return compiler, node


# Each JSON type as a reader of JSON Schema names it.
_JSON_TYPES = {
    "string": "a string",
    "integer": "an integer",
    "number": "a number",
    "boolean": "a boolean",
    "object": "an object",
    "array": "an array",
    "null": "null",
}


def _listed(words: list[str], last: str) -> str:
    if len(words) == 1:
        return words[0]
    return f"{', '.join(words[:-1])} {last} {words[-1]}"


def _number(value: Fraction) -> str:
    # An integer a float holds is written in full up to 2**53, and a larger
    # bound, such as the largest float, as the schema's float.
    whole = value.denominator == 1 and abs(value) <= 2**53
    return str(value.numerator) if whole else repr(float(value))


def _range(span: Interval) -> str:
    low = None if span.low is None else _number(span.low)
    high = None if span.high is None else _number(span.high)
    if low is not None and high is not None and not (span.low_open or span.high_open):
        words = f" from {low} to {high}"
    else:
        parts = []
        if low is not None:
            parts.append(f"greater than {low}" if span.low_open else f"at least {low}")
        if high is not None:
            parts.append(f"less than {high}" if span.high_open else f"at most {high}")
        closed = len(parts) == 1 and parts[0].startswith("at ")
        words = f" {'of ' if closed else ''}{' and '.join(parts)}" if parts else ""
    return words if span.step is None else f"{words}, a multiple of {span.step}"


def _size(low: int | None, high: int | None, unit: str) -> str:
    if low is None and high is None:
        return ""
    if low == high:
        text, count = f"{low}", low
    elif high is None:
        text, count = f"at least {low}", low
    elif low is None:
        text, count = f"at most {high}", high
    else:
        text, count = f"{low} to {high}", high
    return f" of {text} {unit}{'' if count == 1 else 's'}"


def _admitted(compiler: Compiler, node: Any) -> list[str] | None:
    # What a subschema admits, one phrase for each value or branch it lists,
    # read through valgebra-jsonschema's own readings of a union, a list of
    # constants and a numeric range; None for a subschema these do not word.
    node = _followed(compiler, node)
    listed = constants(node)
    if listed is not None:
        return [json.dumps(value) for value in listed]
    split = branches(node)
    if split is not None:
        phrases: list[str] = []
        for branch in split:
            words = _admitted(compiler, branch)
            if words is None:
                return None
            phrases += words
        return phrases
    if not isinstance(node, dict) or node.get("type") not in _JSON_TYPES:
        return None
    kind = node["type"]
    rest = set(node) - ANNOTATIONS - {"type"}
    if kind in ("integer", "number"):
        span = interval(node)
        return None if span is None else [_JSON_TYPES[kind] + _range(span)]
    if kind == "string" and rest <= {"minLength", "maxLength", "pattern"}:
        size = _size(node.get("minLength"), node.get("maxLength"), "character")
        pattern = f" matching {node['pattern']}" if "pattern" in node else ""
        return [_JSON_TYPES[kind] + size + pattern]
    if kind == "array" and rest <= {"items", "minItems", "maxItems"}:
        return [
            _JSON_TYPES[kind]
            + _size(node.get("minItems"), node.get("maxItems"), "item")
        ]
    if kind == "object" or not rest:
        # A record's keys are worded where one of them fails.
        return [_JSON_TYPES[kind]]
    return None


def _keys(contract: Contract, path: tuple[str | int, ...]) -> list[str] | None:
    # The keys the record at `path` declares: the tool's arguments at the top.
    if not path:
        return [slot.path[0] for slot in contract.arguments]
    located = _schema_at(contract, path)
    if located is None:
        return None
    compiler, node = located
    for branch in branches(node) or [node]:
        branch = _followed(compiler, branch)
        if isinstance(branch, dict) and isinstance(branch.get("properties"), dict):
            return list(branch["properties"])
    return None


def _expected(contract: Contract, error: ValidationError) -> str:
    # The expectation in the terms of the schema the client holds, not of the
    # Python values valgebra-jsonschema compiles it into: an unexpected key
    # names the keys its record takes, any other failure what its subschema
    # admits. A failure these words do not cover keeps valgebra's expectation.
    if error.code == "recursion_limit":
        # The value nests past what the server reads; valgebra's expectation
        # counts the levels of its own walk, which no client can count.
        return "a value that nests fewer levels"
    if error.code == "extra_forbidden":
        keys = _keys(contract, error.path[:-1])
        if keys is not None:
            return f"no key but {_listed(keys, 'and')}" if keys else "no key"
    elif error.code != "missing_key":
        located = _schema_at(contract, error.path)
        words = None if located is None else _admitted(*located)
        if words is not None:
            return _listed(words, "or")
    return error.expected


def _names(node: Any) -> set[str]:
    # Every property name a schema declares, at any depth, under `$defs` too.
    names: set[str] = set()
    if isinstance(node, dict):
        properties = node.get("properties")
        if isinstance(properties, dict):
            names |= set(properties)
        for value in node.values():
            names |= _names(value)
    elif isinstance(node, list):
        for item in node:
            names |= _names(item)
    return names


# The keys a refusal writes back, each tool's input schema's own.
DECLARED = {tool["name"]: frozenset(_names(tool["inputSchema"])) for tool in TOOLS}

# The steps of a path a refusal names in full: a longer one, such as a filter
# nested to the depth the server reads, is cut in its middle.
PATH_STEPS_SHOWN = 12


def _where(
    path: tuple[str | int, ...], whole: str, names: frozenset[str] | None = None
) -> str:
    # With `names`, a key outside them is written `*`.
    steps = [
        str(step) if names is None or isinstance(step, int) or step in names else "*"
        for step in path
    ]
    if len(steps) > PATH_STEPS_SHOWN:
        half = PATH_STEPS_SHOWN // 2
        cut = len(steps) - 2 * half
        steps = [*steps[:half], f"<{cut} more>", *steps[-half:]]
    return ".".join(steps) or whole


def refusal(name: str, arguments: dict[str, Any]) -> str:
    """Return why the tool `name` refuses `arguments`, as the client reads it.

    The contract's first failure, by path and expectation: what a client
    changes to be served, without the value it sent, and read no further than
    the first failure, as /api/actions reads a refused filter.
    """
    try:
        CONTRACTS[name].accepts.validate(arguments, fail_fast=True)
    except ValidationError as e:
        where = _where(e.path, "the arguments", DECLARED[name])
        expected = _expected(CONTRACTS[name], e)
        return (
            f"{name} refuses its arguments: at {where}: expected {expected} [{e.code}]"
        )
    return f"{name} refuses its arguments"


def drift(name: str, result: Any) -> str:
    """Return where the result of the tool `name` leaves its contract.

    The first failure's path and code, as valgebra logs a response that
    drifted. The path names the field a fix reads, the code is stable enough
    to count, and neither renders the value.
    """
    returns = CONTRACTS[name].returns
    try:
        if returns is not None:
            returns.validate(result, fail_fast=True)
    except ValidationError as e:
        return f"at {_where(e.path, 'the result')} [{e.code}]"
    return "outside its contract"
