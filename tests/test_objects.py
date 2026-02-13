import time

from bollard import DockerClient


def test_container_lifecycle(docker_client: DockerClient, alpine_image: str) -> None:
    """Test basic container lifecycle: run, inspect, logs, remove."""

    # 1. Run container
    # We use sleep to keep it alive for log check, but we also want it to output something.
    command = "sh -c 'echo Hello World && sleep 2'"
    container = docker_client.run_container(alpine_image, command=command)

    try:
        # 2. Assert ID exists
        assert container.id is not None, "Container ID should not be None"
        assert len(container.id) > 0

        # Reload to get status (run_container now does it, but good to be sure or check updates)
        container.reload()
        assert container.status in ["running", "created"]

        # 3. Wait and check logs
        time.sleep(1)  # Wait for echo to happen
        logs = container.logs()
        assert "Hello World" in logs.strip()

    finally:
        # 4. Cleanup
        container.remove(force=True)
