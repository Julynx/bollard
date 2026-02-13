import uuid
from typing import Generator

import pytest

from bollard import DockerClient


@pytest.fixture
def docker_client() -> Generator[DockerClient, None, None]:
    """Fixture to provide a DockerClient instance and handle cleanup."""
    import bollard

    print(f"DEBUG: bollard imported from {bollard.__file__}")
    with DockerClient() as client:
        yield client


@pytest.fixture
def random_name() -> str:
    """Generate a random name for resources to avoid collisions."""
    return f"bollard-test-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def alpine_image(docker_client: DockerClient) -> str:
    """Ensure alpine:latest is present."""
    image = "alpine:latest"
    # We could check if it exists or just pull it to be safe/update it.
    # For speed in repeated local tests, maybe check first?
    # But pull ensures latest. Let's pull but ignore output for now or log it.
    try:
        docker_client.pull_image(image)
    except Exception:
        # If pull fails (e.g. offline), we hope it exists.
        # In a real CI, we'd want to fail or require network.
        pass
    return image
