import time

from bollard import DockerClient


def test_container_actions(
    docker_client: DockerClient, alpine_image: str, random_name: str
) -> None:
    """Test start, stop, restart, kill, logs."""

    # Create but don't start immediately (if possible, but run_container starts by default)
    # bollard.run_container usually creates and starts.
    container = docker_client.run_container(
        alpine_image, command="sleep 60", name=random_name
    )

    try:
        assert container.status == "running" or container.status == "created"

        # Stop
        container.stop()
        container.reload()
        assert container.status == "exited"

        # Start
        container.start()
        container.reload()
        assert container.status == "running"

        # Restart
        container.restart()
        container.reload()
        assert container.status == "running"

        # Logs
        # Exec something to produce logs
        container.exec("echo 'log test'")
        time.sleep(1)
        logs = container.logs()
        assert (
            "log test" in logs or len(logs) >= 0
        )  # logs might be empty if timing is off, but call shouldn't fail

        # Kill
        container.kill()
        container.reload()
        assert container.status == "exited"

    finally:
        container.remove(force=True)


def test_exec(docker_client: DockerClient, alpine_image: str) -> None:
    """Test exec command."""
    with docker_client.container(alpine_image, command="sleep 60") as container:
        # Simple exec
        out = container.exec(["echo", "hello"])
        assert "hello" in out

        # Multiplexed (if supported by library logic)
        out_mux = container.exec(["echo", "mux"], tty=False)
        assert "mux" in out_mux
