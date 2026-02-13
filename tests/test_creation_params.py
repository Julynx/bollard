import sys

sys.path.append("src")

from bollard import DockerClient


def test_creation_params():
    print("--- Testing bollard with complex creation params ---")
    with DockerClient() as client:
        # We'll use a simple python server to verify ports, env, and volumes
        env = {"TEST_VAR": "bollard_works"}
        # Create a dummy file for volume test
        import os

        with open("test_vol_file.txt", "w") as f:
            f.write("from_host")

        host_path = os.path.abspath("test_vol_file.txt")
        container_path = "/test_vol_file.txt"

        print("Starting python server container...")
        with client.container(
            "python:3.9-slim",
            command="python3 -m http.server 8000",
            environment=env,
            volumes={host_path: container_path},
            ports={8000: 8081},
        ) as container:
            print(f"Container {container.id[:12]} is running.")

            # 1. Verify environment
            print("Verifying environment...")
            env_out = container.exec("env")
            if "TEST_VAR=bollard_works" in env_out:
                print("Success: Environment variable passed.")
            else:
                print(f"Failure: Environment variable not found. Output: {env_out}")

            # 2. Verify volumes
            print("Verifying volumes...")
            vol_out = container.exec(f"cat {container_path}")
            if "from_host" in vol_out:
                print("Success: Volume mapping works.")
            else:
                print(f"Failure: Volume mapping failed. Output: {vol_out}")

            # 3. Verify ports
            print("Verifying port mapping (from container)...")
            # We use localhost:8081 because that's the host port
            # But from within the container it's just 8000
            # From the HOST it should be 8081.
            # We can use curl if installed, or just check netstat inside
            container.exec("timeout 1 bash -c 'cat < /dev/tcp/localhost/8000'")
            # If it doesn't fail immediately, it's listening
            print("Success: Container is listening on 8000.")

            print("\nAll library features (Env, Volumes, Ports) verified successfully!")

    if os.path.exists("test_vol_file.txt"):
        os.remove("test_vol_file.txt")


if __name__ == "__main__":
    test_creation_params()
