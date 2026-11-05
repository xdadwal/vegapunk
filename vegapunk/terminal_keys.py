"""Prompt-scoped enhanced keyboard reporting over prompt_toolkit's VT parser."""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager

from prompt_toolkit.input import Input
from prompt_toolkit.input.vt100_parser import Vt100Parser
from prompt_toolkit.output import Output
from prompt_toolkit.output.vt100 import Vt100_Output

_PUSH = "\x1b[>1u"  # Disambiguate modifiers; no releases or alternate key fields.
_POP = "\x1b[<u"
_CSI_U = re.compile(r"\x1b\[(\d+)(?:;(\d+))?u\Z")
_PASTE_START, _PASTE_END = "\x1b[200~", "\x1b[201~"
_KEYPAD_TEXT = {**{57399 + i: ord(str(i)) for i in range(10)},
                **dict(zip(range(57409, 57417), map(ord, "./*-+\r=,")))}
_KEYPAD_NAV = dict(zip(range(57417, 57428),
                      ((1, "D"), (1, "C"), (1, "A"), (1, "B"), (5, "~"), (6, "~"),
                       (1, "H"), (1, "F"), (2, "~"), (3, "~"), (1, "E"))))
_CTRL_ASCII = {ord(char): chr(value) for char, value in
               ((" ", 0), ("@", 0), ("2", 0), ("3", 27), ("[", 27), ("4", 28),
                ("\\", 28), ("]", 29), ("5", 29), ("6", 30), ("^", 30), ("~", 30),
                ("7", 31), ("_", 31), ("/", 31), ("8", 127), ("?", 127))}


def _legacy_key(sequence: str) -> str:
    """Translate the requested Kitty flag-1 subset into existing editing keys."""
    match = _CSI_U.fullmatch(sequence)
    if match is None:
        return sequence
    code, modifier = int(match[1]), int(match[2] or 1) - 1
    if modifier < 0 or not 0 <= code <= 0x10FFFF or 0xD800 <= code <= 0xDFFF:
        return ""  # Malformed event, never insert control syntax as prose.
    # Caps/num lock do not change shortcut modifiers. Super/Hyper/Meta shortcuts
    # have no existing prompt binding; do not turn them into ordinary typing.
    modifier &= ~192
    if modifier & ~7:
        return ""
    if code in _KEYPAD_NAV:
        number, suffix = _KEYPAD_NAV[code]
        if modifier:
            return f"\x1b[{number};{modifier + 1}{suffix}"
        return f"\x1b[{'' if number == 1 and suffix != '~' else number}{suffix}"
    code = _KEYPAD_TEXT.get(code, code)
    shift, alt, ctrl = bool(modifier & 1), bool(modifier & 2), bool(modifier & 4)
    if code in (8, 127):
        text = "\x08" if ctrl else "\x7f"
    elif code == 13:
        text = "\n" if shift else "\r"
    elif code == 9:
        text = "\x1b[Z" if shift else "\t"
    elif code == 27:
        text = "\x1b"
    elif ctrl:
        if 65 <= code <= 90 or 97 <= code <= 122:
            text = chr((code | 32) - ord("a") + 1)
        elif code <= 127:
            text = _CTRL_ASCII.get(code, chr(code))
        else:
            return ""
    elif code < 32 or code == 127:
        text = chr(code)
    elif 57344 <= code <= 63743:
        # Unbound media/extended function codes are not ordinary text.
        return ""
    else:
        text = chr(code)
        if shift and "a" <= text <= "z":
            text = text.upper()
    return ("\x1b" if alt else "") + text


class KeyboardParser:
    """Frame CSI-u across reads while leaving legacy keys and paste untouched."""

    def __init__(self, parser: Vt100Parser) -> None:
        self.parser = parser
        self.pending = ""
        self.pasting = False
        self.paste_tail = ""

    def feed(self, data: str) -> None:
        index = 0
        while index < len(data):
            if self.pasting:
                combined = self.paste_tail + data[index:]
                end = combined.find(_PASTE_END)
                if end < 0:
                    self.parser.feed(data[index:])
                    self.paste_tail = combined[-(len(_PASTE_END) - 1):]
                    return
                count = end + len(_PASTE_END) - len(self.paste_tail)
                self.parser.feed(data[index:index + count])
                index += count
                self.pasting, self.paste_tail = False, ""
                continue
            char = data[index]
            index += 1
            if not self.pending:
                if char == "\x1b":
                    self.pending = char
                else:
                    # Feed ordinary text in batches (important for long pastes).
                    end = data.find("\x1b", index)
                    end = len(data) if end < 0 else end
                    self.parser.feed(char + data[index:end])
                    index = end
            elif self.pending == "\x1b":
                if char == "[":
                    self.pending += char
                else:
                    self.parser.feed(self.pending)
                    self.pending = ""
                    index -= 1
            else:
                self.pending += char
                if "@" <= char <= "~":
                    sequence, self.pending = self.pending, ""
                    self.parser.feed(_legacy_key(sequence))
                    if sequence == _PASTE_START:
                        self.pasting = True
                elif len(self.pending) > 64 or not " " <= char <= "?":
                    self.parser.feed(self.pending)
                    self.pending = ""

    def flush(self) -> None:
        if self.pending:
            self.parser.feed(self.pending)
            self.pending = ""
        self.parser.flush()


@contextmanager
def enhanced_keyboard(input: Input, output: Output) -> Iterator[None]:
    """Negotiate only while editing, then restore the enclosing terminal mode."""
    parser = getattr(input, "vt100_parser", None)
    if not isinstance(parser, Vt100Parser):
        yield
        return
    input.vt100_parser = KeyboardParser(parser)
    interactive = isinstance(output, Vt100_Output) and output.stdout.isatty()
    try:
        if interactive:
            output.write_raw(_PUSH)
            output.flush()
        yield
    finally:
        try:
            if interactive:
                output.write_raw(_POP)
                output.flush()
        finally:
            input.vt100_parser = parser
