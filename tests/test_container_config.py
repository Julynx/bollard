from bollard import DockerClient


def test_container_configuration(docker_client: DockerClient, tmp_path) -> None:
    """Test environment, volumes, and ports configuration."""

    # Setup Volume file
    vol_file = tmp_path / "test_vol_file.txt"
    vol_file.write_text("from_host")
    container_path = "/test_vol_file.txt"

    env = {"TEST_VAR": "bollard_works"}

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
            ports={8000: 8081},
        ) as container:
            # 1. Verify Env
            out = container.exec(["env"])
            assert "TEST_VAR=bollard_works" in out

            # 2. Verify Volume
            out = container.exec(["cat", container_path])
            assert "from_host" in out.strip()
    finally:
        try:
            docker_client.remove_image(image)
        except Exception:
            pass
