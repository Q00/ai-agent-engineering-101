"""Moved to depts/sales.py (sales: floors, first ask, warranty). This name stays so existing imports, tools and kit hooks keep working:
importing pricing gives the very same module object as depts.sales."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import depts.sales as _m  # noqa: E402

sys.modules[__name__] = _m
