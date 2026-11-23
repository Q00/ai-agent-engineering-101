"""Moved to depts/ceo.py (CEO: shop and buyer case). This name stays so existing imports, tools and kit hooks keep working:
importing strategy gives the very same module object as depts.ceo."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import depts.ceo as _m  # noqa: E402

sys.modules[__name__] = _m
