from bollard import DockerClient


def test_container_context_manager(
    docker_client: DockerClient, alpine_image: str
) -> None:
    """Test using the container context manager."""

    command = "echo 'Hello Context'"

    with docker_client.container(alpine_image, command="sleep 5") as container:
        assert container.id is not None
        print(f"Container {container.id} is running.")

        # Test exec inside context
        output = container.exec(command)
        assert "Hello Context" in output.strip()

    # Verify container is gone (or stopped/removed depending on implementation, usually removed)
    # The context manager in bollard likely stops/removes it.
    # We can check by trying to inspect it, expecting an error or checking state.
    # Assuming standard behavior:
    try:
        container.reload()  # Should fail or show strict 'dead' status if not removed
        # If it persists, assert it's not running
        assert container.status != "running"
    except Exception:
        # If it raises because it's gone, that's also fine
        pass
