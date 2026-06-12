"""A tool that YELLS! response back to User.

Converts model response to UPPERCASE!
"""

from __future__ import annotations

from .registry import tool


@tool
def yell(response: str = "") -> str:
    """Return response arg in UPPERCASE! to act angry and yell!"""
    return response.upper()
