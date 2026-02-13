from tqdm import tqdm

from bollard import DockerClient


def test_list_images(docker_client: DockerClient, alpine_image: str) -> None:
    """Test listing images."""
    images = docker_client.list_images()
    assert len(images) > 0
    # Ensure alpine is in the list (checking by tag or id if possible, simplistically by repotags)
    found = False
    for img in images:
        if any("alpine" in tag for tag in img.tags):
            found = True
            break
    assert found, "alpine image should be present in list_images"


def test_pull_image(docker_client: DockerClient) -> None:
    """Test pulling an image."""
    # Using busybox as a small alternative to alpine to test pull
    image_name = "busybox:latest"
    try:
        # Consume generator
        for _ in tqdm(docker_client.pull_image(image_name)):
            pass

        # Verify it exists
        images = docker_client.list_images()
        found = any(any("busybox" in tag for tag in img.tags) for img in images)
        assert found, "busybox image should be present after pull"
    finally:
        try:
            docker_client.remove_image(image_name)
        except Exception:
            pass
