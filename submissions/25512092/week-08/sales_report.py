"""Moved to depts/qc.py (QC: sales reports, money summary, buying-split history). This name stays so existing imports, tools and kit hooks keep working:
importing sales_report gives the very same module object as depts.qc."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import depts.qc as _m  # noqa: E402

sys.modules[__name__] = _m
