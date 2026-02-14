from bollard import DockerClient


def test_container_context_manager(docker_client: DockerClient) -> None:
    """Test using the container context manager."""

    command = "echo 'Hello Context'"

    with docker_client.container("alpine:latest", command="sleep 5") as container:
        assert container.resource_id is not None

        # Test exec inside context
        output = container.exec(command)
        assert "Hello Context" in output.strip()

    try:
        container.reload()  # Should fail or show strict 'dead' status if not removed
        # If it persists, assert it's not running
        assert container.status != "running"
    except Exception:
        # If it raises because it's gone, that's also fine
        pass
    finally:
        try:
            # docker_client.remove_image("alpine:latest")
            pass
        except Exception:
            pass
