from bollard import DockerClient


def test_copy_operations(
    docker_client: DockerClient, alpine_image: str, tmp_path
) -> None:
    """Test copy_to_container and copy_from_container."""

    # Setup host files in tmp_path
    host_file = tmp_path / "hello.txt"
    host_file.write_text("Hello from host!")

    host_dir = tmp_path / "subdir"
    host_dir.mkdir()
    (host_dir / "sub.txt").write_text("Hello from subdir!")

    # Start container
    with docker_client.container(alpine_image, command="sleep 60") as container:
        # 1. Copy FILE to container
        container.copy_to(str(host_file), "/tmp/")
        out = container.exec(["cat", "/tmp/hello.txt"])
        assert "Hello from host!" in out.strip()

        # 2. Copy DIRECTORY to container
        container.copy_to(str(host_dir), "/tmp/")
        out_dir = container.exec(["cat", "/tmp/subdir/sub.txt"])
        assert "Hello from subdir!" in out_dir.strip()

        # 3. Copy FILE from container
        container.exec(
            ["sh", "-c", "echo 'Hello from container!' > /tmp/from_container.txt"]
        )
        dest_file = tmp_path / "from_container.txt"
        container.copy_from("/tmp/from_container.txt", str(tmp_path))

        assert dest_file.exists()
        assert dest_file.read_text().strip() == "Hello from container!"

        # 4. Copy DIRECTORY from container
        container.exec(["mkdir", "-p", "/tmp/from_dir"])
        container.exec(["sh", "-c", "echo 'in dir' > /tmp/from_dir/file.txt"])

        dest_dir = (
            tmp_path / "from_dir"
        )  # copy_from likely copies the folder itself into the target logic
        # If I copy /tmp/from_dir to tmp_path, it should appear as tmp_path/from_dir

        container.copy_from("/tmp/from_dir", str(tmp_path))
        assert dest_dir.exists()
        assert (dest_dir / "file.txt").read_text().strip() == "in dir"
