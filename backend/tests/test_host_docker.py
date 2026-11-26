import docker.errors
import pytest

from vegapunk.host.docker import DockerError, DockerHost


class FakeContainer:
    def __init__(self, calls, attrs=None, stats=None, missing=False, logs=b""):
        self.attrs = attrs or {}
        self._calls, self._stats, self._missing, self._logs = calls, stats or {}, missing, logs

    def stats(self, **kwargs):
        self._calls.append(("stats", kwargs))
        if self._missing:
            raise docker.errors.NotFound("gone")
        return self._stats

    def logs(self, **kwargs):
        self._calls.append(("logs", kwargs))
        if self._missing:
            raise docker.errors.NotFound("gone")
        return self._logs


class FakeContainers:
    def __init__(self, calls, items):
        self._calls, self._items = calls, items

    def list(self, **kwargs):
        self._calls.append(("list", kwargs))
        return self._items

    def get(self, container_id):
        self._calls.append(("get", container_id))
        for item in self._items:
            if item.attrs.get("Id") == container_id:
                return item
        raise docker.errors.NotFound("gone")


class FakeClient:
    def __init__(self, items=()):
        self.calls = []
        self.containers = FakeContainers(self.calls, list(items))

    def ping(self):
        self.calls.append(("ping", {}))
        return True

    def info(self):
        self.calls.append(("info", {}))
        return {"Name": "nas"}

    def version(self):
        self.calls.append(("version", {}))
        return {"Version": "29.0"}


def test_public_surface_is_exactly_the_read_methods():
    public = {n for n in dir(DockerHost) if not n.startswith("_")}
    assert public == {
        "ping",
        "info",
        "version",
        "list_containers",
        "container_stats",
        "container_logs",
    }


def test_only_read_endpoints_are_ever_called():
    client = FakeClient([FakeContainer([], {"Id": "a"}, {"cpu_stats": {}})])
    host = DockerHost("unix:///x", client=client)
    host.ping()
    host.info()
    assert host.version() == "29.0"
    host.list_containers()
    host.container_stats("a")
    host.container_logs("a", 5)
    assert {name for name, _ in client.calls} <= {
        "ping",
        "info",
        "version",
        "list",
        "get",
        "stats",
        "logs",
    }


def test_list_containers_asks_for_all_states_and_ignores_removed_ones():
    client = FakeClient([FakeContainer([], {"Id": "a"})])
    assert DockerHost("unix:///x", client=client).list_containers() == [{"Id": "a"}]
    assert ("list", {"all": True, "ignore_removed": True}) in client.calls


def test_stats_are_one_shot():
    client = FakeClient()
    client.containers = FakeContainers(client.calls, [])
    container = FakeContainer(client.calls, {"Id": "a"}, {"ok": 1})
    client.containers._items.append(container)
    assert DockerHost("unix:///x", client=client).container_stats("a") == {"ok": 1}
    assert ("stats", {"stream": False, "one_shot": True}) in client.calls


def test_a_vanished_container_has_no_stats():
    host = DockerHost("unix:///x", client=FakeClient())
    assert host.container_stats("nope") is None


def test_stats_of_a_container_that_vanishes_mid_call_is_none():
    client = FakeClient([FakeContainer([], {"Id": "a"}, missing=True)])
    assert DockerHost("unix:///x", client=client).container_stats("a") is None


def test_client_is_built_with_a_five_second_timeout(monkeypatch):
    seen = {}

    def build(**kwargs):
        seen.update(kwargs)
        return FakeClient()

    monkeypatch.setattr("vegapunk.host.docker.docker.DockerClient", build)
    DockerHost("unix:///x").ping()
    assert seen == {"base_url": "unix:///x", "timeout": 5}


def test_construction_never_connects():
    DockerHost("unix:///definitely/not/here.sock")


def test_a_client_that_failed_to_build_is_retried(monkeypatch):
    attempts = []

    def build(**kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise docker.errors.DockerException("not yet")
        return FakeClient()

    monkeypatch.setattr("vegapunk.host.docker.docker.DockerClient", build)
    host = DockerHost("unix:///x")
    with pytest.raises(DockerError):
        host.ping()
    host.ping()
    assert len(attempts) == 2


def _chained(*errors):
    """Build an exception whose __cause__ chain runs through the given errors, like the SDK's."""
    outer = docker.errors.DockerException("Error while fetching server API version")
    current = outer
    for error in errors:
        current.__cause__ = error
        current = error
    return outer


class Broken:
    def __init__(self, exc):
        self.exc = exc

    def ping(self):
        raise self.exc

    info = version = ping


def test_dead_socket_is_a_readable_docker_error():
    exc = _chained(ConnectionError("conn"), FileNotFoundError(2, "No such file"))
    with pytest.raises(DockerError, match=r"Can't reach Docker at unix:///dead\.sock"):
        DockerHost("unix:///dead.sock", client=Broken(exc)).ping()


def test_wrapped_permission_error_mentions_docker_gid():
    exc = _chained(ConnectionError("conn"), PermissionError(13, "Permission denied"))
    with pytest.raises(DockerError, match="DOCKER_GID"):
        DockerHost("unix:///var/run/docker.sock", client=Broken(exc)).info()


def test_unknown_failure_keeps_its_text():
    with pytest.raises(DockerError, match="something odd"):
        DockerHost("unix:///x", client=Broken(RuntimeError("something odd"))).version()


def _logs_host(logs=b"", missing=False):
    client = FakeClient()
    container = FakeContainer(client.calls, {"Id": "a"}, logs=logs, missing=missing)
    client.containers._items.append(container)
    return DockerHost("unix:///x", client=client), client


def test_container_logs_asks_for_timestamps_both_streams_and_the_tail():
    host, client = _logs_host(b"x\n")
    host.container_logs("a", 50)
    assert (
        "logs",
        {"tail": 50, "timestamps": True, "stdout": True, "stderr": True},
    ) in client.calls


def test_container_logs_splits_decodes_and_survives_bad_bytes():
    host, _ = _logs_host(b"first\r\nb\xff\nlast\n")
    assert host.container_logs("a", 10) == ["first", "b\ufffd", "last"]


def test_container_logs_with_no_output_is_an_empty_list():
    host, _ = _logs_host(b"")
    assert host.container_logs("a", 10) == []


def test_container_logs_of_a_vanished_container_is_none():
    assert DockerHost("unix:///x", client=FakeClient()).container_logs("nope", 10) is None
    host, _ = _logs_host(missing=True)
    assert host.container_logs("a", 10) is None


def test_container_logs_failure_is_a_docker_error():
    with pytest.raises(DockerError):
        DockerHost("unix:///x", client=Broken(RuntimeError("boom"))).container_logs("a", 10)
