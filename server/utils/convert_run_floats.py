#!/usr/bin/env python3
"""Convert the int values unfinished runs store where the schema states a float.

Experimental and optional. A run created before the float fields were written
as floats can hold an int zero where the schema asks for a float: the SPRT
log-likelihood ratio and overshoot sums, the internal throughput, a bad
task's residual. The server only logs such a run, and the value becomes a
float as the run updates or leaves the check when the run finishes. This
converts it at once.

The fields come from the schema's own report: every `float_type` failure
whose stored value is an int, and not a bool, is set to the same number as a
double. Nothing else is written, and a finished run is not read.

Run it with every fishtest instance stopped: the primary holds unfinished
runs in its cache and writes them back whole, which undoes the conversion.
It writes nothing without --apply.

    cd server
    uv run python utils/convert_run_floats.py [--db NAME] [--apply]
"""

import argparse
from datetime import UTC

from bson.codec_options import CodecOptions
from pymongo import MongoClient
from valgebra import ValidationError

from fishtest.schemas import runs_schema
from fishtest.util import FISHTEST


def resolve(document, path):
    """Return the value at an error path, and the path as a dotted MongoDB key."""
    parts = []
    for part in path:
        document = document[part]
        parts.append(str(part))
    return document, ".".join(parts)


def int_float_fields(run):
    """Map each field where the run stores an int and the schema states a
    float, as a dotted MongoDB path, to the same number as a float."""
    try:
        runs_schema.validate(run)
    except ValidationError as e:
        errors = e.errors
    else:
        return {}
    fields = {}
    for error in errors:
        if error["code"] != "float_type":
            continue
        value, key = resolve(run, error["path"])
        # A bool is an int too, and a bool where a float belongs is not a
        # number written the old way.
        if type(value) is int:
            fields[key] = float(value)
    return fields


def convert(db, apply):
    """Convert the unfinished runs, or with `apply` false only list them.

    Returns the number of runs and of fields converted or to convert.
    """
    runs = fields = 0
    for run in db["runs"].find({"finished": False}):
        update = int_float_fields(run)
        if not update:
            continue
        runs += 1
        fields += len(update)
        print(f"{run['_id']} v{run.get('version', '?')}: {', '.join(sorted(update))}")
        if apply:
            db["runs"].update_one(
                {"_id": run["_id"], "finished": False}, {"$set": update}
            )
    verb = "converted" if apply else "to convert, run with --apply to write them"
    print(f"{runs} runs, {fields} fields {verb}")
    return runs, fields


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=FISHTEST, help="database name")
    parser.add_argument("--apply", action="store_true", help="write the conversion")
    args = parser.parse_args()
    # The server's own codec, so the runs validate as the server reads them.
    codec_options = CodecOptions(tz_aware=True, tzinfo=UTC)
    with MongoClient("localhost") as client:
        db = client[args.db].with_options(codec_options=codec_options)
        convert(db, args.apply)


if __name__ == "__main__":
    main()
