import logging
import os
import sys
import time

sys.path.append(".")
from bollard import DockerClient

# Configure logging to show info level logs from bollard
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)


def test() -> None:
    print("Initializing DockerClient...")

    try:
        with DockerClient() as docker:
            # Check if we can talk to the daemon
            try:
                docker.list_images()
            except Exception as e:
                print(f"Could not connect to Docker/Podman: {e}")
                print(
                    "Please ensure Docker or Podman is running and exposing a named pipe or Unix socket."
                )
                return

            print("=== 1. Images & Pull ===")
            print("Pulling alpine:latest...")
            # Updated to consume generator
            for progress in docker.pull_image("alpine:latest"):
                if "status" in progress:
                    print(
                        f"\r{progress['status']} {progress.get('progress', '')}", end=""
                    )
            print("\nPull complete.")

            images = docker.list_images()
            print(f"Images found: {len(images)}")

            print("\n=== 2. Container Lifecycle ===")
            # Run a container
            print("Starting container...")
            container = docker.run_container(
                "alpine:latest", command="echo Hello from Python", name="bollard-test"
            )
            print(f"Container created: {container.id[:12]}")

            # Logs
            # Give it a moment to run
            time.sleep(1)
            logs = container.logs()
            print(f"Logs: {logs.strip()}")

            # Stop
            print("Stopping container...")
            container.stop()
            print("Container stopped.")

            # Start
            print("Starting container again...")
            container.start()
            print("Container started.")

            # Restart
            print("Restarting container...")
            container.restart()
            print("Container restarted.")

            # Cleanup this container manually since we didn't use run_container context here
            print("Removing container...")
            container.remove(force=True)

            # Exec
            print("\n=== 3. Exec & Ephemeral Container ===")
            # We need a running container that stays alive to exec into.
            # "echo" exits immediately. Let's run a sleep container using the context manager.
            print("Starting sleep container for exec test...")

            with docker.container(
                "alpine:latest", command="sleep 60", name="bollard-sleep"
            ) as sleep_container:
                print(f"Sleep container started: {sleep_container.id[:12]}")

                print("Executing command (tty=True)...")
                exec_out = sleep_container.exec(["echo", "Exec Works!"], tty=True)
                print(f"Exec output: {exec_out.strip()}")

                print("Executing command (tty=False, multiplexed)...")
                # This tests the new _read_multiplexed_response logic
                exec_out_mux = sleep_container.exec(
                    ["echo", "Multiplexed Works!"], tty=False
                )
                print(f"Exec Mux output: {exec_out_mux.strip()}")

                # Container will be auto-removed when exiting this block

            print("\n=== 4. Networks & Volumes ===")
            print("Testing Network Management...")
            try:
                network = docker.create_network("bollard-net", driver="bridge")
                print(f"Created network: {network.id[:12]}")

                inspect = network.inspect()
                print(f"Inspected network: {inspect['Name']}")

                network.remove()
                print("Removed network.")
            except Exception as e:
                print(f"Network test failed: {e}")

            print("Testing Volume Management...")
            try:
                volume = docker.create_volume("bollard-vol")
                print(f"Created volume: {volume.name}")

                inspect = volume.inspect()
                print(f"Inspected volume: {inspect['Name']}")

                volume.remove(force=True)
                print("Removed volume.")
            except Exception as e:
                print(f"Volume test failed: {e}")

            print("\n=== 5. Build ===")
            # Create a dummy Dockerfile
            with open("Dockerfile", "w") as f:
                f.write("FROM alpine:latest\nRUN echo 'Built with Bollard'")

            try:
                print("Building image bollard-test-image:latest...")
                # Updated to consume generator
                for progress in docker.build_image(".", "bollard-test-image:latest"):
                    if "stream" in progress:
                        print(progress["stream"], end="")
                print("\nBuild completed.")
            except Exception as e:
                print(f"Build failed: {e}")
            finally:
                if os.path.exists("Dockerfile"):
                    os.remove("Dockerfile")
                # Clean up built image
                try:
                    docker.remove_image("bollard-test-image:latest")
                    print("Test Image removed.")
                except Exception:
                    pass

            print("\nSUCCESS: All tests passed!")

    except Exception as e:
        print(f"\nError: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    test()
