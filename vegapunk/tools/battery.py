"""A tool that reports the device's battery level.

Deliberately factual: it returns the raw charge and charging state only.
Vegapunk's *personality* (its mood by battery level) lives in the system prompt,
not here — so this tool stays reusable for any other purpose.
"""

from __future__ import annotations

import psutil

from .registry import tool


@tool
def get_battery() -> str:
    """Read the device's current battery charge percentage and whether it is charging."""
    battery = psutil.sensors_battery()
    if battery is None:
        # No battery (desktop, or AC-only) — report honestly rather than guess.
        return "No battery detected (this device may be a desktop or on AC power only)."
    state = "charging" if battery.power_plugged else "on battery"
    return f"Battery is at {round(battery.percent)}% ({state})."
