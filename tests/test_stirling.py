import os
import time

import pytest
from tqdm import tqdm

from bollard import DockerClient


@pytest.mark.integration
def test_stirling_pdf_conversion(docker_client: DockerClient):
    """
    Integration test for Stirling-PDF HTML to PDF conversion.
    Verifies container run, service availability, command execution, and file copy.
    """
    image = "frooodle/s-pdf"

    # Ensure image is pulled (this might take a while the first time)
    # Most environments would pre-pull this or have it cached.
    try:
        for _ in tqdm(docker_client.pull_image(image)):
            pass
    except Exception:
        pytest.skip(f"Could not pull {image}, skipping integration test")

    env = {"SECURITY_ENABLE_LOGIN": "false"}

    with docker_client.container(image, environment=env) as container:
        # 1. Wait for service to be ready
        ready = False
        for _ in range(60):  # Increased timeout for slow environments
            try:
                res = container.exec("curl -s -I http://localhost:8080")
                if "HTTP" in res:
                    ready = True
                    break
            except Exception:
                pass
            time.sleep(1)

        assert ready, "Stirling-PDF service did not become ready in time"

        # 2. Create input file
        container.exec("sh -c 'echo \"<h1>Test PDF</h1>\" > /test.html'")

        # 3. Perform conversion
        # We check for 200 OK
        cmd = (
            'curl -s -w "%{http_code}" '
            "-F 'fileInput=@/test.html' "
            "http://localhost:8080/api/v1/convert/html/pdf "
            "-o /test.pdf"
        )
        status_code = container.exec(cmd)
        assert status_code == "200", f"Conversion failed with status {status_code}"

        # 4. Verify output file exists in container
        file_list_result = container.exec("ls -l /test.pdf")
        assert "/test.pdf" in file_list_result

        # 5. Copy file to host
        test_output = "stirling_test_output.pdf"
        if os.path.exists(test_output):
            os.remove(test_output)

        try:
            container.copy_from("/test.pdf", ".")
            # copy_from might put it in the current dir if '.' is used,
            # or we might need to be careful with paths.
            # Base logic of copy_from: tar.extractall(path=destination_path)
            # If source is /test.pdf, it will extract to ./test.pdf
            assert os.path.exists("test.pdf")
            os.rename("test.pdf", test_output)
            assert os.path.getsize(test_output) > 0
        finally:
            if os.path.exists(test_output):
                os.remove(test_output)
            if os.path.exists("test.pdf"):
                os.remove("test.pdf")
