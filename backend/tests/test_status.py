import pytest

from vegapunk.capture.status import container_status


@pytest.mark.parametrize(
    "state,health,expected",
    [
        ("running", None, "online"),
        ("running", "healthy", "online"),
        ("running", "starting", "online"),
        ("running", "unhealthy", "degraded"),
        ("restarting", None, "degraded"),
        ("paused", None, "degraded"),
        ("exited", None, "offline"),
        ("dead", None, "offline"),
        ("created", None, "unknown"),
        ("removing", None, "unknown"),
        ("something-new", None, "unknown"),
    ],
)
def test_container_status(state, health, expected):
    assert container_status(state, health) == expected
