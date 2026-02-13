from bollard import DockerClient


def test_build_image(docker_client: DockerClient, random_name: str, tmp_path) -> None:
    """Test building an image from Dockerfile."""

    # Create Dockerfile in temporary directory
    dockerfile_content = "FROM alpine:latest\nRUN echo 'Built with Bollard'"
    dockerfile_path = tmp_path / "Dockerfile"
    dockerfile_path.write_text(dockerfile_content)

    image_tag = f"bollard-test-{random_name}:latest"

    # Build
    try:
        # Assuming build_image takes path and tag
        # The library likely needs string path, not Path object if strictly typed without support
        build_gen = docker_client.build_image(str(tmp_path), image_tag)
        for chunk in build_gen:
            pass  # Consume output

        # Verify image exists
        images = docker_client.list_images()
        found = any(any(image_tag in tag for tag in img.tags) for img in images)
        assert found, "Built image should exist"

    finally:
        try:
            docker_client.remove_image(image_tag)
        except Exception:
            pass
