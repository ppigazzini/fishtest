"""How the MCP server words a refused call and logs a refused result.

A call outside its contract is answered with its first failures: each one's
path, what the input schema admits there in JSON Schema's terms, which the
client holds, and the keyword that refuses it. valgebra-jsonschema names the
keyword and where it stands (`Compiler.located`); the words are read there
through its readers, `branches`, `constants`, `interval`, `length` and
`ANNOTATIONS`, and a subschema they do not word keeps valgebra's expectation. A
key the contract does not declare is written `*`: it is text the caller chose.
A result outside its contract is logged by its first failures' paths and
keywords. Neither names the value.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from fractions import Fraction
from typing import Any

from valgebra import ValidationError, Validator
from valgebra_jsonschema import (
    ANNOTATIONS,
    Compiler,
    Interval,
    branches,
    constants,
    interval,
    length,
)
from valgebra_planning.contracts import contract_names

from fishtest.mcp.contracts import CONTRACTS, TOOLS

SCHEMAS = {tool["name"]: tool for tool in TOOLS}

# The failures a refusal lists: the full report's first ones.
FAILURES_SHOWN = 3


def _followed(compiler: Compiler, node: Any) -> Any:
    # A subschema with each reference followed to the schema it names.
    while isinstance(node, dict) and "$ref" in node:
        node = compiler.resolve(node["$ref"])
    return node


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


def _size(node: dict[str, Any], unit: str) -> str | None:
    # The length a sized schema bounds, its pattern and its items aside; None
    # for one that asserts more.
    rest = {key: node[key] for key in node if key not in ("pattern", "items")}
    span = length(rest)
    if span is None:
        return None
    low, high = span.least, span.most
    if high is None:
        if not low:
            return ""
        text, count = f"at least {low}", low
    elif low == high:
        text, count = f"{low}", low
    else:
        text, count = f"{low} to {high}" if low else f"at most {high}", high
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
    if kind in ("string", "array"):
        size = _size(node, "character" if kind == "string" else "item")
        if size is None:
            return None
        pattern = f" matching {node['pattern']}" if "pattern" in node else ""
        return [_JSON_TYPES[kind] + size + pattern]
    if kind == "object" or not rest:
        # A record's keys are worded where one of them fails.
        return [_JSON_TYPES[kind]]
    return None


def _expected(compiler: Compiler, error: dict[str, Any]) -> str:
    # The expectation in the terms of the schema the client holds, not of the
    # Python values valgebra-jsonschema compiles it into: an unexpected key
    # names the keys its record takes, any other failure what the schema at
    # its keyword admits. A failure these words do not cover keeps valgebra's.
    if error["code"] == "recursion_limit":
        # The value nests past what the server reads; valgebra's expectation
        # counts the levels of its own walk, which no client can count.
        return "a value that nests fewer levels"
    if "keyword" in error and error["keyword"] != "required":
        # The object the failing keyword sits in is the subschema at the
        # location's parent: at the location itself, a keyword whose value is a
        # schema, as `additionalProperties: false`, names that subschema.
        node = compiler.at(error["keywordLocation"].rpartition("/")[0])
        if error["keyword"] == "additionalProperties":
            keys = list(node.get("properties", ()))
            return f"no key but {_listed(keys, 'and')}" if keys else "no key"
        words = _admitted(compiler, node)
        if words is not None:
            return _listed(words, "or")
    return error["expected"]


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


def _failures(
    values: Validator[Any], compiler: Compiler, value: Any
) -> Iterator[dict[str, Any]]:
    # The first failures, each with the keyword that makes it. A compiler keeps
    # state as it reads, so a call reads its failures through one of its own.
    try:
        values.validate(value, limit=FAILURES_SHOWN)
    except ValidationError as e:
        for error in e.errors:
            if error["code"] == "recursion_limit":
                # Found past the depth the walk reads, so by no keyword.
                yield dict(error)
            else:
                yield compiler.located(error, value)


def _once(lines: Iterator[str]) -> str:
    # Two keywords refusing one value in the same words are one line.
    return "; ".join(dict.fromkeys(lines))


def refusal(name: str, arguments: dict[str, Any]) -> str:
    """Return why the tool `name` refuses `arguments`, as the client reads it.

    The contract's first failures, by path, expectation and keyword: what a
    client changes to be served, without the value it sent, and read no
    further than those.
    """
    contract = CONTRACTS[name]
    compiler = Compiler(SCHEMAS[name]["inputSchema"])
    names = contract_names(contract)
    why = _once(
        f"at {_where(error['path'], 'the arguments', names)}:"
        f" expected {_expected(compiler, error)}"
        f" [{error.get('keyword', error['code'])}]"
        for error in _failures(contract.accepts, compiler, arguments)
    )
    return f"{name} refuses its arguments: {why}"


def drift(name: str, result: Any) -> str:
    """Return where the result of the tool `name` leaves its contract.

    The first failures' paths and keywords. A path names the field a fix reads,
    and a keyword is stable enough to count; neither renders the value.
    """
    returns = CONTRACTS[name].returns
    if returns is None:
        return "outside its contract"
    compiler = Compiler(SCHEMAS[name]["outputSchema"])
    return _once(
        f"at {_where(error['path'], 'the result')}"
        f" [{error.get('keyword', error['code'])}]"
        for error in _failures(returns, compiler, result)
    )
