import sys

sys.path.append("src")
import time

from bollard import DockerClient

with DockerClient() as client:
    import os

    hf_token = os.environ.get("HF_TOKEN", "")
    hf_cache = os.path.expanduser("~/.cache/huggingface")

    # vLLM requires a model to be specified at startup.
    # We use parameters matching the user's docker run command.
    command = "--model Qwen/Qwen3-0.6B --device cpu"

    print("--- Creating vLLM container ---")
    print("Image: vllm/vllm-openai:latest")
    print(f"Command: {command}")

    container = client.run_container(
        "docker.io/vllm/vllm-openai:latest",
        command=command,
        environment={"HF_TOKEN": hf_token},
        volumes={hf_cache: "/root/.cache/huggingface"},
        ports={8000: 8001},
        ipc_mode="host",
    )

    try:
        print(f"Container {container.id[:12]} is running.")

        print("Waiting for vLLM server to start (this can take a minute)...")
        # Retry loop for the server to be ready
        for i in range(60):
            try:
                # Try to list models to check if server is up
                # Note: vLLM might take a while to actually listen on the port
                output = container.exec(
                    "curl -s -X POST http://localhost:8001/v1/models"
                )
                if "Qwen" in output:
                    print(f"Success! Output: {output}")
                    break
            except Exception as e:
                # If the error indicates container is not running, stop early and show logs
                if "state improper" in str(e) or "not running" in str(e):
                    print(f"\n[!] Container crashed or stopped. Error: {e}")
                    print("--- CONTAINER LOGS ---")
                    print(container.logs(tail=100))
                    print("----------------------")
                    sys.exit(1)

                if i % 10 == 0:
                    print(f"Still waiting... ({i}/60)")

            time.sleep(2)
        else:
            print("Timed out waiting for vLLM server.")
            print("--- CONTAINER LOGS ---")
            print(container.logs(tail=100))
            print("----------------------")
            sys.exit(1)

    finally:
        # We always cleanup in the test unless we manually stop it
        # print(f"Removing container {container.id[:12]}...")
        # container.remove(force=True)
        pass
