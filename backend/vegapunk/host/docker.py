"""Read-only access to Docker. Nothing else in Vegapunk talks to the Docker socket.

The public methods are exactly the reads Vegapunk needs. Adding a method that changes
something on the host (start, stop, exec) is a deliberate, reviewed step, not a side effect.
"""

from typing import Any

import docker
import docker.errors

TIMEOUT_SECONDS = 5


class DockerError(Exception):
    """Docker could not be read. The message is safe to show people."""


def _chain(exc: BaseException) -> list[BaseException]:
    found: list[BaseException] = []
    while exc is not None and exc not in found:
        found.append(exc)
        exc = exc.__cause__ or exc.__context__  # type: ignore[assignment]
    return found


def _describe(exc: BaseException, url: str) -> str:
    chain = _chain(exc)
    if any(isinstance(e, PermissionError) for e in chain):
        return (
            f"Vegapunk can't read the Docker socket at {url}. "
            "Set DOCKER_GID to the group that owns it."
        )
    unreachable = (FileNotFoundError, ConnectionError, TimeoutError)
    if any(
        isinstance(e, unreachable) or type(e).__name__.endswith(("ConnectionError", "Timeout"))
        for e in chain
    ):
        return f"Can't reach Docker at {url}."
    return str(exc) or type(exc).__name__


class DockerHost:
    def __init__(self, url: str, client: Any = None) -> None:
        self._url = url
        self._client = client

    def _docker(self) -> Any:
        # The SDK connects inside its constructor. A client that failed to build is not
        # kept, so the next call tries again (Docker may simply not be up yet).
        if self._client is None:
            self._client = docker.DockerClient(base_url=self._url, timeout=TIMEOUT_SECONDS)
        return self._client

    def _read(self, action):
        try:
            return action(self._docker())
        except DockerError:
            raise
        except Exception as exc:
            raise DockerError(_describe(exc, self._url)) from exc

    def ping(self) -> None:
        self._read(lambda c: c.ping())

    def info(self) -> dict:
        return self._read(lambda c: c.info())

    def version(self) -> str:
        return self._read(lambda c: c.version()["Version"])

    def list_containers(self) -> list[dict]:
        # ignore_removed: a container deleted while Docker is being listed is skipped
        # instead of failing the whole listing.
        return self._read(
            lambda c: [x.attrs for x in c.containers.list(all=True, ignore_removed=True)]
        )

    def container_stats(self, container_id: str) -> dict | None:
        def stats(c):
            try:
                return c.containers.get(container_id).stats(stream=False, one_shot=True)
            except docker.errors.NotFound:
                return None

        return self._read(stats)

    def container_logs(self, container_id: str, tail: int) -> list[str] | None:
        """The last `tail` lines of a container's output, oldest first. None if it is gone."""

        def logs(c):
            try:
                raw = c.containers.get(container_id).logs(
                    tail=tail, timestamps=True, stdout=True, stderr=True
                )
            except docker.errors.NotFound:
                return None
            lines = raw.decode("utf-8", errors="replace").split("\n")
            if lines and lines[-1] == "":
                lines.pop()
            return [line.removesuffix("\r") for line in lines]

        return self._read(logs)
