"""Minimal `pyramid.view` stubs.

The real Pyramid decorators drive route registration and view lookup.
Here we only preserve importability and allow tests to attach metadata.
"""

from __future__ import annotations


def view_config(**kw):
    def deco(fn):
        configs = getattr(fn, "__view_configs__", None)
        if configs is None:
            configs = []
            setattr(fn, "__view_configs__", configs)
        configs.append(dict(kw))
        return fn

    return deco


def view_defaults(**kw):
    def deco(obj):
        setattr(obj, "__view_defaults__", dict(kw))
        return obj

    return deco


def exception_view_config(*args, **kw):
    def deco(fn):
        setattr(fn, "__exception_view_config__", {"args": args, **kw})
        return fn

    return deco


def forbidden_view_config(**kw):
    def deco(fn):
        setattr(fn, "__forbidden_view_config__", dict(kw))
        return fn

    return deco


def notfound_view_config(**kw):
    def deco(fn):
        setattr(fn, "__notfound_view_config__", dict(kw))
        return fn

    return deco
