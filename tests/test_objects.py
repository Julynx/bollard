import sys

sys.path.append(".")
import time

from bollard import DockerClient, container

with DockerClient() as client:
    for progress in client.pull_image("alpine:latest"):
        print(progress)

    container = client.run_container("alpine:latest", command="echo 'Hello World'")

    # Now we can use the object!
    print(f"Container ID: {container.id}")
    time.sleep(1)  # Wait for it to finish
    print(f"Logs: {container.logs().strip()}")

    container.remove(force=True)
