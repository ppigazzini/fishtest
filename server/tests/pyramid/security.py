"""Minimal `pyramid.security` stubs."""

from __future__ import annotations


def remember(request, userid, max_age=None):
    request._remember = True
    return []


def forget(request):
    request._forget = True
    return []
