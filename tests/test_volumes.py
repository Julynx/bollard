from bollard import DockerClient


def test_volume_lifecycle(docker_client: DockerClient, random_name: str) -> None:
    """Test create, inspect, remove volume."""
    volume_name = f"{random_name}-vol"

    try:
        volume = docker_client.create_volume(volume_name)
        assert volume.name == volume_name

        # Inspect
        info = volume.inspect()
        assert info["Name"] == volume_name

    finally:
        try:
            if "volume" in locals():
                volume.remove(force=True)
        except Exception:
            pass
