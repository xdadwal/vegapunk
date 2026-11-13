"""Enhanced keyboard input is negotiated and decoded without altering pastes."""

import io

import pytest
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.input.vt100_parser import Vt100Parser
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.output.vt100 import Vt100_Output
from prompt_toolkit.data_structures import Size

from vegapunk.prompter import PromptToolkitPrompter
from vegapunk.terminal_keys import KeyboardParser, _legacy_key


def test_enhanced_control_keys_are_editing_events_not_literal_input():
    with create_pipe_input() as inp:
        inp.send_text('hello café\x1b[127;3uthere\x1b[13u')
        prompt = PromptToolkitPrompter(history=InMemoryHistory(), input=inp, output=DummyOutput())
        assert prompt.prompt() == 'hello there'


@pytest.mark.parametrize('key', ['\x1b[99;5u', '\x1b[100;5u'])
def test_enhanced_interrupt_and_eof_keep_existing_behavior(key):
    with create_pipe_input() as inp:
        inp.send_text(key)
        prompt = PromptToolkitPrompter(history=InMemoryHistory(), input=inp, output=DummyOutput())
        with pytest.raises(KeyboardInterrupt if '99;' in key else EOFError):
            prompt.prompt()


@pytest.mark.parametrize('events,expected', [
    ('one two\x1b[97;5uX\r', 'Xone two'),
    ('first\x1b[106;5usecond\r', 'first\nsecond'),
    ('first\x1b[13;2usecond\r', 'first\nsecond'),
    ('one two\x1b[127;5uX\r', 'one X'),
    ('one two\x1b[98;3uX\r', 'one Xtwo'),
    ('x\x1b[97;2u\r', 'xA'),
])
def test_enhanced_keys_preserve_shortcuts(events, expected):
    with create_pipe_input() as inp:
        inp.send_text(events)
        assert PromptToolkitPrompter(history=InMemoryHistory(), input=inp, output=DummyOutput()).prompt() == expected


def test_parser_handles_split_sequences_and_bracketed_paste_literally():
    keys = []
    parser = KeyboardParser(Vt100Parser(keys.append))
    text = '\x1b[200~literal \x1b[127;3u\n中\x1b[201~\x1b[99;5u'
    for char in text:
        parser.feed(char)
    parser.flush()
    assert keys[0].data == 'literal \x1b[127;3u\n中'
    assert keys[1].data == '\x03'


def test_plain_escape_navigation_and_terminal_reports_survive():
    sequences = ['\x1b', '\x1b[A', '\x1b[Z', '\x1b[10;20R', '\x1b[3;3~']
    for sequence in sequences:
        expected, actual = [], []
        legacy = Vt100Parser(expected.append)
        adapted = KeyboardParser(Vt100Parser(actual.append))
        legacy.feed(sequence); legacy.flush()
        adapted.feed(sequence); adapted.flush()
        assert [(k.key, k.data) for k in actual] == [(k.key, k.data) for k in expected]


@pytest.mark.parametrize('enhanced,legacy', [
    ('\x1b[57414u', '\r'), ('\x1b[57417u', '\x1b[D'),
    ('\x1b[57426u', '\x1b[3~'), ('\x1b[57417;2u', '\x1b[1;2D'),
    ('\x1b[57399u', '0'), ('\x1b[57413u', '+'),
    ('\x1b[50;5u', '\0'), ('\x1b[47;5u', '\x1f'),
])
def test_keypad_and_ascii_control_events_keep_legacy_meaning(enhanced, legacy):
    assert _legacy_key(enhanced) == legacy


def test_non_ascii_control_key_cannot_crash_the_decoder():
    assert _legacy_key('\x1b[304;5u') == ''


class TerminalStream(io.StringIO):
    def isatty(self):
        return True


@pytest.mark.parametrize('events,error', [('ok\r', None), ('\x03', KeyboardInterrupt), ('\x04', EOFError)])
def test_protocol_push_pop_and_parser_restore_on_prompt_exit(events, error):
    stream = TerminalStream()
    output = Vt100_Output(stream, lambda: Size(rows=24, columns=80), enable_cpr=False)
    with create_pipe_input() as inp:
        parser = inp.vt100_parser
        inp.send_text(events)
        prompt = PromptToolkitPrompter(history=InMemoryHistory(), input=inp, output=output)
        if error:
            with pytest.raises(error):
                prompt.prompt()
        else:
            assert prompt.prompt() == 'ok'
        assert inp.vt100_parser is parser
        assert stream.getvalue().count('\x1b[>1u') == 1
        assert stream.getvalue().count('\x1b[<u') == 1


def test_nonterminal_output_has_no_protocol_escape_sequences():
    stream = io.StringIO()
    output = Vt100_Output(stream, lambda: Size(rows=24, columns=80), enable_cpr=False)
    with create_pipe_input() as inp:
        inp.send_text('ok\r')
        assert PromptToolkitPrompter(history=InMemoryHistory(), input=inp, output=output).prompt() == 'ok'
    assert '\x1b[>1u' not in stream.getvalue() and '\x1b[<u' not in stream.getvalue()


def test_unexpected_prompt_error_also_restores_mode_and_parser(monkeypatch):
    stream = TerminalStream()
    output = Vt100_Output(stream, lambda: Size(rows=24, columns=80), enable_cpr=False)
    with create_pipe_input() as inp:
        original = inp.vt100_parser
        prompt = PromptToolkitPrompter(history=InMemoryHistory(), input=inp, output=output)
        before_prompt = stream.tell()
        def failed():
            raise RuntimeError('synthetic prompt failure')
        monkeypatch.setattr(prompt._session, 'prompt', failed)
        with pytest.raises(RuntimeError, match='synthetic prompt failure'):
            prompt.prompt()
        assert inp.vt100_parser is original
    assert stream.getvalue()[before_prompt:] == '\x1b[>1u\x1b[<u'
