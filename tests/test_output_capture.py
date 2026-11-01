"""Pre-clipping capture is bounded and task-local."""

from vegapunk import output_capture
import shlex
import sys
import pytest


def test_capture_retains_text_before_tool_preview_clipping():
    output_capture.begin()
    output_capture.record("full output", complete=True)
    saved = output_capture.finish()
    assert saved.text == "full output"
    assert saved.complete
    assert output_capture.finish() is None


def test_concurrent_capture_state_survives_thread_handoff_without_cross_talk():
    import asyncio

    async def capture(text):
        output_capture.begin()
        await asyncio.to_thread(output_capture.record, text)
        await asyncio.sleep(0)
        return output_capture.finish().text

    async def run():
        return await asyncio.gather(capture("one"), capture("two"))

    assert asyncio.run(run()) == ["one", "two"]


def test_unicode_capture_boundary_marks_incomplete(monkeypatch):
    monkeypatch.setattr(output_capture, "LIMIT", 5)
    output_capture.begin()
    output_capture.record("ééé")
    saved = output_capture.finish()
    assert saved.text == "éé"
    assert saved.complete is False


def test_streaming_shell_drains_excess_without_retaining_it(tmp_path, monkeypatch):
    from vegapunk import shell_capture

    monkeypatch.setattr(shell_capture, "LIMIT", 64)
    command = f"{shlex.quote(sys.executable)} -c \"print('x'*10000)\""
    result = shell_capture.run(command, cwd=str(tmp_path), input="", timeout=5)
    assert result.returncode == 0
    assert len(result.stdout) == 64
    assert result.capture_complete is False


def test_inherited_stdout_pipe_obeys_shell_deadline(tmp_path):
    import os
    import subprocess
    from vegapunk import shell_capture

    if os.name != "posix":
        pytest.skip("POSIX process-group cleanup contract")
    with pytest.raises(subprocess.TimeoutExpired):
        shell_capture.run("sleep 5 & exit 0", cwd=str(tmp_path), input="", timeout=0.1)


def test_timeout_after_stdout_closes_retains_observed_output(tmp_path):
    import os
    import subprocess
    from vegapunk import shell_capture

    if os.name != "posix":
        pytest.skip("POSIX shell descriptor semantics")
    with pytest.raises(subprocess.TimeoutExpired) as error:
        shell_capture.run("printf before; exec 1>&- 2>&-; sleep 2", cwd=str(tmp_path), input="", timeout=0.1)
    assert error.value.output == "before"
