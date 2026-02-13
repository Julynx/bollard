import os
import time

import pytest

from bollard import DockerClient


@pytest.mark.integration
def test_vllm_integration(docker_client: DockerClient) -> None:
    """
    Integration test for vLLM container.
    This test pulls a large image and requires significant resources.
    It should likely be skipped in normal CI unless configured.
    """
    # Check if we should run this test
    if not os.environ.get("RUN_INTEGRATION_TESTS"):
        pytest.skip("Skipping integration test. Set RUN_INTEGRATION_TESTS=1 to run.")

    hf_token = os.environ.get("HF_TOKEN", "")
    hf_cache = os.path.expanduser("~/.cache/huggingface")

    if not hf_token:
        pytest.skip("HF_TOKEN not set")

    command = "--model Qwen/Qwen3-0.6B --device cpu"
    image = "docker.io/vllm/vllm-openai:latest"

    # Ensure image is pulled (this might take a while)
    # docker_client.pull_image(image) # implicit in run_container usually

    try:
        container = docker_client.run_container(
            image,
            command=command,
            environment={"HF_TOKEN": hf_token},
            volumes={hf_cache: "/root/.cache/huggingface"},
            ports={8000: 8001},
            ipc_mode="host",
        )

        # Wait logic
        max_retries = 60
        for i in range(max_retries):
            try:
                # Check status
                container.reload()
                if container.status != "running":
                    logs = container.logs(tail=100)
                    pytest.fail(f"Container stopped unexpectedly: {logs}")

                # Curl check
                output = container.exec(
                    "curl -s -X POST http://localhost:8001/v1/models"
                )
                if "Qwen" in output:
                    break  # Success
            except Exception:
                pass

            time.sleep(2)
        else:
            logs = container.logs(tail=100)
            pytest.fail(f"Timed out waiting for vLLM. Logs: {logs}")

    finally:
        # Cleanup is handled by fixture if we used yield,
        # but here we manually created it attached to client.
        # Use try-finally to ensure cleanup of this heavy container.
        try:
            if "container" in locals():
                container.remove(force=True)
        except Exception:
            pass
