import uuid
from typing import Generator

import pytest

from bollard import DockerClient


@pytest.fixture
def docker_client() -> Generator[DockerClient, None, None]:
    """Fixture to provide a DockerClient instance and handle cleanup."""
    with DockerClient() as client:
        yield client


@pytest.fixture
def random_name() -> str:
    """Generate a random name for resources to avoid collisions."""
    return f"bollard-test-{uuid.uuid4().hex[:8]}"
