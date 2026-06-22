
"""A tool that reports system performance metrics (CPU, RAM, Disk, Network).

This tool gathers system resource usage and returns the data in a structured
JSON format for easy consumption by the agent's brain.
"""

from __future__ import annotations

import psutil
import json

from .registry import tool # Assuming this import is correct based on project structure

@tool
def get_system_stats() -> str:
    """Gathers and returns CPU, RAM, Disk, and Network usage statistics in JSON format."""
    stats = {}

    # CPU Usage
    stats['cpu_usage'] = psutil.cpu_percent(interval=0.1)

    # RAM Usage
    mem = psutil.virtual_memory()
    stats['ram_usage_percent'] = mem.percent
    stats['ram_total_gb'] = round(mem.total / (1024**3), 2)
    stats['ram_used_gb'] = round(mem.used / (1024**3), 2)

    # Return the stats as a structured JSON string
    return json.dumps(stats, indent=4)
