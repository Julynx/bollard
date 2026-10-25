"""Unit tests for the container-start resilience in bollard.

Rootless engines without full cgroup v2 delegation (Podman machines in
WSL) reject resource limits at start time; these tests lock the
retry-without-limits behavior that keeps the pod booting there.
"""

from bollard import DockerException
from bollard.container import Container
from bollard.image import Image

CGROUP_ERROR = DockerException(
    "Docker API Error (500): "
    '{"cause":"crun: open `memory.max` for writing: No such file or directory"}'
)
PIDS_ERROR = DockerException(
    "Docker API Error (500): "
    '{"cause":"crun: open `pids.max` for writing: No such file or directory"}'
)


class StubClient:
    """Records API calls and scripts the create/start outcomes."""

    def __init__(self, start_outcomes, fail_first_create=False):
        self.calls: list[tuple[str, str, dict | None]] = []
        self.start_outcomes = list(start_outcomes)
        self.creations = 0
        self.last_id: str | None = None
        self.fail_first_create = fail_first_create

    def _request(self, method, endpoint, body=None, **kwargs):
        self.calls.append((method, endpoint.split("?")[0], body))
        if method == "POST" and endpoint == "/containers/create":
            self.creations += 1
            if self.fail_first_create and self.creations == 1:
                raise DockerException("Docker API Error (404): no such image")
            self.last_id = f"container-{self.creations}"
            return {"Id": self.last_id}
        if method == "POST" and endpoint.endswith("/start"):
            outcome = self.start_outcomes.pop(0) if self.start_outcomes else None
            if outcome is not None:
                raise outcome
            return {}
        if method == "DELETE":
            return {}
        if method == "GET" and endpoint.endswith("/json"):
            return {"Id": self.last_id or "unknown"}
        return {}


def last_create_body(client: StubClient) -> dict:
    bodies = [
        body
        for method, endpoint, body in client.calls
        if method == "POST" and endpoint == "/containers/create"
    ]
    return bodies[-1]


def test_start_retries_without_resource_limits(monkeypatch):
    monkeypatch.setattr(Image, "pull", lambda client, image: None)
    client = StubClient(start_outcomes=[CGROUP_ERROR, None])
    payload = {
        "Image": "python:3.14-trixie",
        "HostConfig": {"Init": True, "Memory": 8589934592, "PidsLimit": 512},
    }
    container = Container._create_and_start(
        client, "/containers/create", payload, "python:3.14-trixie", None
    )
    assert container.resource_id == "container-2"
    assert client.creations == 2
    retry_body = last_create_body(client)
    assert "Memory" not in retry_body["HostConfig"]
    assert "PidsLimit" not in retry_body["HostConfig"]
    assert retry_body["HostConfig"]["Init"] is True, "unrelated keys survive"
    assert ("DELETE", "/containers/container-1", None) in client.calls, (
        "the limited container is removed before retrying"
    )


def test_start_retries_after_pull_flow_too(monkeypatch):
    """The pull-then-create path also recovers from the limit error."""
    monkeypatch.setattr(Image, "pull", lambda client, image: None)
    client = StubClient(start_outcomes=[CGROUP_ERROR, None], fail_first_create=True)
    payload = {"Image": "python:3.14-trixie", "HostConfig": {"Memory": 1}}
    container = Container._create_and_start(
        client, "/containers/create", payload, "python:3.14-trixie", None
    )
    assert container.resource_id == "container-3", (
        "the 404-failed create and the limited one both count as creations"
    )
    assert "Memory" not in last_create_body(client)["HostConfig"]


def test_other_start_errors_are_not_retried(monkeypatch):
    monkeypatch.setattr(Image, "pull", lambda client, image: None)
    client = StubClient(start_outcomes=[DockerException("port already allocated")])
    payload = {"Image": "python:3.14-trixie", "HostConfig": {"Memory": 1}}
    raised = False
    try:
        Container._create_and_start(
            client, "/containers/create", payload, "python:3.14-trixie", None
        )
    except DockerException as exc:
        raised = True
        assert "port already allocated" in str(exc)
    assert raised, "the unrelated error must propagate"
    assert client.creations == 1, "no retry for unrelated failures"


def test_pids_limit_failures_are_covered(monkeypatch):
    monkeypatch.setattr(Image, "pull", lambda client, image: None)
    client = StubClient(start_outcomes=[PIDS_ERROR, None])
    payload = {"Image": "python:3.14-trixie", "HostConfig": {"PidsLimit": 512}}
    container = Container._create_and_start(
        client, "/containers/create", payload, "python:3.14-trixie", None
    )
    assert container.resource_id == "container-2"
    assert "PidsLimit" not in last_create_body(client)["HostConfig"]
