import os
import shutil

import sys

sys.path.append(".")
from bollard import DockerClient


def test_copy() -> None:
    print("Testing copy_to_container and copy_from_container...")

    # Setup local files
    os.makedirs("test_data", exist_ok=True)
    with open("test_data/hello.txt", "w") as f:
        f.write("Hello from host!")

    os.makedirs("test_data/subdir", exist_ok=True)
    test_dir = "test_data/subdir"
    os.makedirs(test_dir, exist_ok=True)
    with open(os.path.join(test_dir, "sub.txt"), "w") as f:
        f.write("Hello from subdir!")

    try:
        with DockerClient() as client:
            test_file = "test_data/hello.txt"

            # Start a container
            print("Starting container...")
            with client.container("alpine:latest", command="sleep 60") as container:
                print(f"Container started: {container.id[:12]}")

                # 1. Copy FILE to container
                print("Copying file to container...")
                container.copy_to(test_file, "/tmp/")

                # Verify content inside
                # we can use docker exec
                out = container.exec(["cat", "/tmp/hello.txt"])
                print(f"Cat output: {out.strip()}")
                if out.strip() != "Hello from host!":
                    print("ERROR: File content mismatch!")

                # 2. Copy DIRECTORY to container
                print("Copying directory to container...")
                container.copy_to(test_dir, "/tmp/")

                out_dir = container.exec(["cat", "/tmp/subdir/sub.txt"])
                print(f"Cat output: {out_dir.strip()}")
                if out_dir.strip() != "Hello from subdir!":
                    print("ERROR: Directory content mismatch!")

                # 3. Copy FILE from container
                print("Copying file from container...")
                # Create a file inside first
                container.exec(
                    [
                        "sh",
                        "-c",
                        "echo 'Hello from container!' > /tmp/from_container.txt",
                    ]
                )

                container.copy_from("/tmp/from_container.txt", ".")

                if os.path.exists("from_container.txt"):
                    with open("from_container.txt", "r") as f:
                        content = f.read().strip()
                        print(f"Read content: {content}")
                        if content != "Hello from container!":
                            print("ERROR: Content mismatch from container!")
                    os.remove("from_container.txt")
                else:
                    print("ERROR: File not copied from container!")

                # 4. Copy DIRECTORY from container
                print("Copying directory from container...")
                container.exec(["mkdir", "-p", "/tmp/from_dir"])
                container.exec(["sh", "-c", "echo 'in dir' > /tmp/from_dir/file.txt"])

                container.copy_from("/tmp/from_dir", ".")

                if os.path.exists("from_dir/file.txt"):
                    with open("from_dir/file.txt", "r") as f:
                        content = f.read().strip()
                        print(f"Read content: {content}")
                        if content != "in dir":
                            print("ERROR: Content mismatch from container directory!")
                    shutil.rmtree("from_dir")
                else:
                    print("ERROR: Directory not copied from container!")

    finally:
        # Cleanup
        if os.path.exists("test_data"):
            shutil.rmtree("test_data")
        # The new code copies directly to the current directory, so no "test_output" to clean up.
        # if os.path.exists("test_output"):
        #     shutil.rmtree("test_output")

    print("SUCCESS: All copy tests passed!")


if __name__ == "__main__":
    test_copy()
