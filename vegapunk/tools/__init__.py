"""Vegapunk's tools — the things it can actually *do*.

Importing each tool module runs its ``@tool`` decorator, which registers the
tool into ``REGISTRY``. ``ALL_TOOLS`` is that populated registry. To add a tool:
create a module with an ``@tool`` function and import it here.
"""

from .base import Tool
from .registry import REGISTRY, tool

# Import tool modules for their side effect: each @tool registers itself.
from . import battery, clock, filesystem, grep, shell, yell, fetch, search, system_stats, memory  # noqa: E402,F401 — imported for registration

ALL_TOOLS = REGISTRY

__all__ = ["Tool", "tool", "ALL_TOOLS"]
