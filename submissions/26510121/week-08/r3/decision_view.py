"""Private, request-scoped reuse of historical reads; never caches payments.

Factories return internal read-only facts. Nothing persists across decisions.
Current inventory, reservations, balances and checkout state bypass this view.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import inspect

_ACTIVE = ContextVar('r3_decision_view', default=None)


@contextmanager
def frame(journal):
    current = _ACTIVE.get()
    if current is not None and current[0] is journal:
        yield
        return
    token = _ACTIVE.set((journal, {}))
    try:
        yield
    finally:
        _ACTIVE.reset(token)


def fact(journal, key, factory):
    current = _ACTIVE.get()
    if current is None or current[0] is not journal:
        return factory()
    cache = current[1]
    if key not in cache:
        cache[key] = factory()
    return cache[key]


def history(journal, kind, limit=500):
    current = _ACTIVE.get()
    if current is not None and current[0] is journal:
        for key, rows in current[1].items():
            if isinstance(key, tuple) and len(key) == 3 and key[:2] == ('history', kind) and key[2] >= limit:
                return rows[:limit]
    return fact(journal, ('history', kind, limit), lambda: journal.events(kind, limit))


def decision(function):
    index = list(inspect.signature(function).parameters).index('journal')
    @wraps(function)
    def wrapped(*args, **kwargs):
        journal = args[index] if len(args) > index else kwargs['journal']
        with frame(journal):
            return function(*args, **kwargs)
    return wrapped
