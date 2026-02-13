import sys

sys.path.append(".")
from bollard import DockerClient

with DockerClient() as client:
    with client.container("alpine:latest") as container:
        print(f"Container {container.id} is running.")
        output = container.exec("echo 'Hello World'")
        print(f"Output: {output}")
