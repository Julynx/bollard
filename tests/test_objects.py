import time

from bollard import DockerClient


def test_container_lifecycle(docker_client: DockerClient) -> None:
    """Test basic container lifecycle: run, inspect, logs, remove."""

    # 1. Run container
    command = "sh -c 'echo Hello World && sleep 2'"
    container = docker_client.run_container("alpine:latest", command=command)

    try:
        # 2. Assert ID exists
        assert container.resource_id is not None, "Container ID should not be None"
        assert len(container.resource_id) > 0

        container.reload()
        assert container.status in ["running", "created"]

        # 3. Wait and check logs
        time.sleep(1)
        logs = container.logs()
        assert "Hello World" in logs.strip()

    finally:
        # 4. Cleanup
        container.remove(force=True)
        try:
            docker_client.remove_image("alpine:latest")
        except Exception:
            pass
