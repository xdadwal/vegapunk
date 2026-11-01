"""Bounded streaming shell output on POSIX without detached reader threads."""

import os
import selectors
import signal
import subprocess
import tempfile
import time

from .output_capture import LIMIT


def run(command: str, *, cwd: str, input: str, timeout: float,
        shell: bool = True, capture_output: bool = True, text: bool = True) -> subprocess.CompletedProcess:
    """Drain merged output while retaining at most LIMIT bytes; kill the group on timeout."""
    if os.name != "posix":
        # The existing Windows behavior remains supported; POSIX is the bounded path.
        return subprocess.run(command, cwd=cwd, input=input, timeout=timeout, shell=shell,
                              capture_output=capture_output, text=text)
    kept = bytearray()
    observed = 0
    deadline = time.monotonic() + timeout
    with tempfile.TemporaryFile() as incoming:
        incoming.write(input.encode("utf-8"))
        incoming.seek(0)
        process = subprocess.Popen(command, shell=shell, cwd=cwd, stdin=incoming,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        assert process.stdout is not None
        settled = False
        try:
            os.set_blocking(process.stdout.fileno(), False)
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(command, timeout, output=kept.decode("utf-8", errors="replace"))
                    for key, _ in selector.select(min(remaining, 0.1)):
                        chunk = os.read(key.fd, 65536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        observed += len(chunk)
                        kept.extend(chunk[:max(0, LIMIT - len(kept))])
            try:
                code = process.wait(timeout=max(0.001, deadline - time.monotonic()))
            except subprocess.TimeoutExpired as exc:
                raise subprocess.TimeoutExpired(command, timeout, output=kept.decode("utf-8", errors="replace")) from exc
            settled = True
            try:
                decoded = kept.decode("utf-8")
                decoded_complete = True
            except UnicodeDecodeError:
                decoded = kept.decode("utf-8", errors="replace")
                decoded_complete = False
            result = subprocess.CompletedProcess(command, code, stdout=decoded, stderr="")
            result.capture_complete = observed <= LIMIT and decoded_complete
            result.capture_note = "byte limit" if observed > LIMIT else "invalid UTF-8"
            return result
        finally:
            # Include inherited pipes/children in the deadline and cleanup, even
            # when the shell has exited but a child still holds stdout open.
            if not settled:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.stdout.close()
            process.wait()
