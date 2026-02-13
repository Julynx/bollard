from bollard import DockerClient


def test_list_images(docker_client: DockerClient) -> None:
    """Test pulling an image and then listing images."""
    # Using busybox as a small alternative to alpine to test pull
    image_name = "busybox:latest"
    try:
        # Consume generator - NOW RETURNS IMAGE
        docker_client.pull_image(image_name)

        # Verify it exists
        images = docker_client.list_images()
        found = any(any("busybox" in tag for tag in img.tags) for img in images)
        assert found, "busybox image should be present after pull"
    finally:
        try:
            docker_client.remove_image(image_name)
        except Exception:
            pass
