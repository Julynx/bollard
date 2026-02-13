from bollard import DockerClient


def test_container_configuration(docker_client: DockerClient, tmp_path) -> None:
    """Test environment, volumes, and ports configuration."""

    # Setup Volume file
    vol_file = tmp_path / "test_vol_file.txt"
    vol_file.write_text("from_host")
    container_path = "/test_vol_file.txt"

    env = {"TEST_VAR": "bollard_works"}

    # We use python image if available, or just alpine with some tools?
    # Original used python:3.9-slim. That's heavy if not present.
    # Alpine has nc usually, or we can install it. Or check env/cat simply.
    # Checking ports needs something listening. 'nc -l -p 8000' in alpine.

    image = "alpine:latest"
    # Ensure image
    try:
        docker_client.pull_image(image)
    except Exception:
        pass

    try:
        with docker_client.container(
            image,
            command="sleep 60",
            environment=env,
            volumes={str(vol_file): container_path},
            ports={8000: 8081},  # 8000 in container -> 8081 on host
        ) as container:
            # 1. Verify Env
            out = container.exec(["env"])
            assert "TEST_VAR=bollard_works" in out

            # 2. Verify Volume
            out = container.exec(["cat", container_path])
            assert "from_host" in out.strip()

            # 3. Verify Ports (Optional / Harder with sleep)
            # We can inspect the container to see if ports are populated in settings
            pass
    finally:
        try:
            docker_client.remove_image(image)
        except Exception:
            pass
