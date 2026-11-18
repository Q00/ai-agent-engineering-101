"""Moved to depts/cs.py (CS: forum queues and posts). This name stays so existing imports, tools and kit hooks keep working:
importing forum_post gives the very same module object as depts.cs."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import depts.cs as _m  # noqa: E402

sys.modules[__name__] = _m
