"""The Tool type — a uniform shape the loop can describe to the model and run.

A tool is just a Python function plus a JSON-Schema description of its inputs,
so the model knows when and how to call it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict  # JSON Schema describing the tool's input arguments
    func: Callable[[dict], str]

    def to_schema(self) -> dict:
        """Render this tool in the OpenAI 'function' tool format the model expects."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def run(self, arguments: dict) -> str:
        return self.func(arguments)
