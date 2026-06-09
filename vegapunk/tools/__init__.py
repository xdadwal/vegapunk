"""Vegapunk's tools — the things it can actually *do*."""

from .base import Tool
from .battery import battery_tool

# Every tool Vegapunk has access to. Register new tools by adding them here.
ALL_TOOLS: list[Tool] = [battery_tool]

__all__ = ["Tool", "ALL_TOOLS", "battery_tool"]
