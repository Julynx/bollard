"""Unit tests for the engine endpoint discovery and machine bring-up.

These tests do not talk to a container engine: the podman subprocess
and the named-pipe probes are stubbed out, so only the orchestration
logic is exercised.
"""

import json
from pathlib import Path

import pytest

from bollard import DockerClient
from bollard.const import WINDOWS_PIPE_CANDIDATES

DOCKER_PIPE = "\\\\.\\pipe\\docker_engine"
PODMAN_PIPE = "\\\\.\\pipe\\podman-machine-default"


def make_client(monkeypatch, reachable=(), machine_calls=None) -> DockerClient:
    """Build a client with the pipe probes and podman commands stubbed.

    `reachable` is the sequence of pipes `_check_pipe` reports as open.
    `machine_calls` receives every podman command argument list invoked.
    """
    client = DockerClient.__new__(DockerClient)
    client._conn = None
    client.socket_path = ""
    monkeypatch.setattr(
        client, "_check_pipe", lambda pipe: pipe in reachable, raising=True
    )
    if machine_calls is not None:
        monkeypatch.setattr(
            client,
            "_run_podman",
            lambda args, timeout: (machine_calls.append(args) or (True, "")),
            raising=True,
        )
    return client


def test_windows_discovery_returns_existing_pipe(monkeypatch):
    client = make_client(monkeypatch, reachable={DOCKER_PIPE})
    brought_up = []
    monkeypatch.setattr(
        client, "_bring_up_podman_machine", lambda: brought_up.append(True)
    )
    assert client._discover_windows_pipe() == DOCKER_PIPE
    assert brought_up == [], "an existing pipe must not trigger the bring-up"


def test_windows_discovery_starts_a_stopped_machine(monkeypatch):
    calls: list[list[str]] = []
    client = DockerClient.__new__(DockerClient)
    client._conn = None
    client.socket_path = ""
    state = {"started": False}

    def fake_run_podman(args, timeout=None):
        calls.append(args)
        if tuple(args) == ("machine", "start"):
            state["started"] = True
        return (True, "")

    def fake_wait(timeout):
        return DOCKER_PIPE if state["started"] else None

    monkeypatch.setattr(client, "_run_podman", fake_run_podman, raising=True)
    monkeypatch.setattr(client, "_wait_for_pipe", fake_wait, raising=True)
    assert client._discover_windows_pipe() == DOCKER_PIPE
    assert ["machine", "start"] in calls
    assert ["machine", "init"] not in calls, "a startable machine needs no init"


def test_windows_discovery_initializes_a_missing_machine(monkeypatch):
    calls: list[list[str]] = []
    client = DockerClient.__new__(DockerClient)
    client._conn = None
    client.socket_path = ""
    state = {"starts": 0}

    def fake_run_podman(args, timeout=None):
        calls.append(args)
        key = tuple(args)
        if key == ("machine", "start"):
            state["starts"] += 1
            return (state["starts"] > 1, "")
        if key == ("machine", "init"):
            return (True, "")
        return (True, "")

    def fake_wait(timeout):
        return PODMAN_PIPE if state["starts"] > 1 else None

    monkeypatch.setattr(client, "_run_podman", fake_run_podman, raising=True)
    monkeypatch.setattr(client, "_wait_for_pipe", fake_wait, raising=True)
    assert client._discover_windows_pipe() == PODMAN_PIPE
    assert ["machine", "init"] in calls, "a missing machine must be initialized"
    assert calls.count(["machine", "start"]) == 2


def test_windows_discovery_raises_when_all_fails(monkeypatch):
    client = make_client(monkeypatch, reachable=set())
    monkeypatch.setattr(
        client,
        "_bring_up_podman_machine",
        lambda: "`podman machine start` failed: boom",
    )
    monkeypatch.setattr(client, "_wait_for_pipe", lambda timeout: None)
    with pytest.raises(FileNotFoundError) as excinfo:
        client._discover_windows_pipe()
    assert "boom" in str(excinfo.value)


def test_bring_up_initializes_only_after_start_fails(monkeypatch):
    calls: list[list[str]] = []
    client = DockerClient.__new__(DockerClient)
    client._conn = None
    client.socket_path = ""

    state = {"starts": 0}

    def fake_run_podman(args, timeout=None):
        calls.append(args)
        key = tuple(args)
        if key == ("machine", "start"):
            state["starts"] += 1
            return (state["starts"] > 1, "")
        if key == ("machine", "inspect"):
            return (False, "no machine exists")
        return (True, "")

    monkeypatch.setattr(client, "_run_podman", fake_run_podman, raising=True)
    monkeypatch.setattr(client, "_wait_for_pipe", lambda timeout: None)
    assert client._bring_up_podman_machine() is None
    assert calls == [
        ["machine", "start"],
        ["machine", "inspect"],
        ["machine", "init"],
        ["machine", "start"],
    ]


def test_bring_up_restarts_a_running_machine_with_a_dead_endpoint(monkeypatch):
    calls: list[list[str]] = []
    client = DockerClient.__new__(DockerClient)
    client._conn = None
    client.socket_path = ""

    state = {"starts": 0}

    def fake_run_podman(args, timeout=None):
        calls.append(args)
        key = tuple(args)
        if key == ("machine", "start"):
            state["starts"] += 1
            return (state["starts"] > 1, "")
        if key == ("machine", "inspect"):
            return (True, '[{"Name": "podman-machine-default"}]')
        return (True, "")

    monkeypatch.setattr(client, "_run_podman", fake_run_podman, raising=True)
    monkeypatch.setattr(client, "_wait_for_pipe", lambda timeout: None)
    assert client._bring_up_podman_machine() is None
    assert ["machine", "stop"] in calls, "a dead running machine gets restarted"
    assert calls.count(["machine", "start"]) == 2
    assert ["machine", "init"] not in calls


def test_bring_up_reports_combined_failures(monkeypatch):
    client = DockerClient.__new__(DockerClient)
    client._conn = None
    client.socket_path = ""
    monkeypatch.setattr(
        client,
        "_run_podman",
        lambda args, timeout=None: (False, f"{args[-1]} broke"),
        raising=True,
    )
    monkeypatch.setattr(client, "_wait_for_pipe", lambda timeout: None)
    failure = client._bring_up_podman_machine()
    assert failure is not None
    assert "start broke" in failure
    assert "init broke" in failure


def test_macos_discovery_uses_docker_host(monkeypatch):
    client = make_client(monkeypatch, reachable=set())
    monkeypatch.setenv("DOCKER_HOST", "unix:///tmp/podman.sock")
    assert client._discover_macos_socket() == "unix:///tmp/podman.sock"


def test_macos_discovery_finds_the_machine_socket(monkeypatch):
    client = DockerClient.__new__(DockerClient)
    client._conn = None
    client.socket_path = ""
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    socket_path = Path("/tmp/podman-machine.sock")
    inspect = json.dumps(
        [
            {
                "Name": "podman-machine-default",
                "ConnectionInfo": {
                    "PodmanSocket": {"Path": str(socket_path)},
                    "DockerSocket": {"Path": str(socket_path)},
                },
            }
        ]
    )
    monkeypatch.setattr(
        client,
        "_run_podman",
        lambda args, timeout=None: (True, inspect),
        raising=True,
    )
    monkeypatch.setattr(Path, "exists", lambda self: str(self) == str(socket_path))
    assert client._discover_macos_socket() == str(socket_path)


def test_run_podman_returns_combined_output(monkeypatch):
    client = DockerClient.__new__(DockerClient)
    client._conn = None
    client.socket_path = ""

    class Completed:
        returncode = 1
        stdout = "out"
        stderr = "err"

    monkeypatch.setattr(
        "bollard.client.subprocess.run", lambda *a, **k: Completed(), raising=True
    )
    ok, output = client._run_podman(["machine", "list"], 5.0)
    assert not ok
    assert output == "outerr"


def test_windows_discovery_reuses_bollard_candidates():
    assert DOCKER_PIPE in WINDOWS_PIPE_CANDIDATES
    assert PODMAN_PIPE in WINDOWS_PIPE_CANDIDATES

