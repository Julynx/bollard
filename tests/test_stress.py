import time

from bollard import DockerClient


def test_large_file_copy(docker_client: DockerClient, tmp_path):
    """
    Test copying a large file (50MB) to and from a container.
    This verifies that we are streaming and not crashing.
    """
    # Create a 50MB file
    large_file = tmp_path / "large.bin"
    size = 50 * 1024 * 1024
    with open(large_file, "wb") as file_obj:
        file_obj.seek(size - 1)
        file_obj.write(b"\0")

    assert large_file.stat().st_size == size

    with docker_client.container("alpine:latest", command="sleep 60") as container:
        # Copy TO container
        container.copy_to(str(large_file), "/tmp/")

        # Verify checking size inside
        out = container.exec(["ls", "-l", "/tmp/large.bin"])
        assert str(size) in out or str(size // 1024) in out  # ls output varies

        # Copy FROM container
        container.copy_from("/tmp/large.bin", str(tmp_path))

        expected_dest = tmp_path / "large.bin"  # tar extract preserves name
        # Wait, copy_from extracts to dest_path (directory)

        assert expected_dest.exists()
        assert expected_dest.stat().st_size == size


def test_dockerignore_support(docker_client: DockerClient, tmp_path):
    """
    Test that .dockerignore files are respected during build.
    """
    context_dir = tmp_path / "context"
    context_dir.mkdir()

    (context_dir / "Dockerfile").write_text("FROM alpine:latest\nCOPY . /app\n")
    (context_dir / "keep.txt").write_text("keep")
    (context_dir / "ignore.txt").write_text("ignore me")
    (context_dir / ".dockerignore").write_text("ignore.txt\n")

    tag = "bollard-ignore-test"

    docker_client.build_image(str(context_dir), tag)

    try:
        with docker_client.container(tag, command="ls /app") as container:
            files = container.logs()
            assert "keep.txt" in files
            assert "ignore.txt" not in files
    finally:
        try:
            docker_client.remove_image(tag, force=True)
        except Exception:
            pass


def test_log_streaming(docker_client: DockerClient):
    """
    Test streaming logs from a container.
    """
    cmd = "sh -c 'echo line1; sleep 1; echo line2; sleep 1; echo line3'"
    with docker_client.container("alpine:latest", command=cmd) as container:
        # Wait a bit for start
        time.sleep(0.5)

        lines = list(container.logs(stream=True, follow=True))
        # Normalize line endings
        lines = [line.replace("\r\n", "\n") for line in lines]
        assert "line1\n" in lines
        assert "line2\n" in lines
        assert "line3\n" in lines


def test_exec_streaming(docker_client: DockerClient):
    """
    Test streaming exec output.
    """
    with docker_client.container("alpine:latest", command="sleep 10") as container:
        cmd = ["sh", "-c", "echo hello; sleep 1; echo world"]

        # Exec with stream=True
        output_gen = container.exec(cmd, stream=True)
        lines = list(output_gen)

        full_output = "".join(lines)
        assert "hello" in full_output
        assert "world" in full_output
