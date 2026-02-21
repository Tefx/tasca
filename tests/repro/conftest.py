"""Exclude repro directory from pytest collection.

The issue_l3_mcp_smoke.py file contains functions named test_* that are
not actual pytest tests - they're smoke test scripts that require manual
setup. This file tells pytest to ignore this directory during collection.
"""

import pytest

# Ignore all files in this directory during test collection
collect_ignore = ["issue_l3_mcp_smoke.py"]
