# Bollard

A Pythonic, zero-dependency client for the Docker and Podman Engine APIs.  
Prioritizes descriptive naming, context managers, and cross-platform ease of use.

## Installation

```bash
pip install bollard
```

## Project Structure

For a detailed overview of the project's architecture, see [ARCHITECTURE.md](ARCHITECTURE.md).

## Key Features

- **Pythonic API**: `list_containers` instead of `ps`; `remove_image` instead of `rmi`.
- **Zero Dependencies**: Uses only the Python standard library (`http.client`, `socket`, `json`).
- **Smart Connection**: Auto-detects Docker/Podman sockets (Unix, Windows Pipes, `DOCKER_HOST`).
- **Windows Friendly**: Auto-starts the Podman machine on Windows if connection fails.
- **Resource Safety**: Context managers for client connections and ephemeral containers.
- **Streaming Output**: Real-time progress updates for long-running operations like pull and build.
- **.dockerignore Support**: Respects `.dockerignore` files when building images.
- **Full Lifecycle**: Manage Containers, Images, Networks, and Volumes.

## Usage

### Basic Connection

```python
from bollard import DockerClient

with DockerClient() as client:
    for image in client.list_images():
        print(f"Image: {image.tags[0]}")
```

### Managing Containers

```python
with DockerClient() as client:
    # Run a container and get logs
    container = client.run_container("alpine:latest", command="echo 'Hello World'")
    print(container.logs())

    # Stop and remove
    container.stop()
    container.remove(force=True)
```

### Ephemeral Containers (Auto-Cleanup)

Use `ephemeral_container` to automatically remove the container after the block exits, even if errors occur.

```python
with DockerClient() as client:
    with client.ephemeral_container("alpine", command="sleep 60") as container:
        container.exec(["echo", "Running inside container"])
    # Container is automatically removed
```

### Streaming Image Operations

Operations like `pull_image`, `build_image`, and `push_image` return a generator that yields progress updates.

```python
with DockerClient() as client:
    # Pull an image with progress
    for progress in client.pull_image("alpine:latest"):
        if "status" in progress:
            print(f"{progress['status']} {progress.get('progress', '')}")

    # Build from directory
    for log in client.build_image(".", "my-app:latest"):
        if "stream" in log:
            print(log["stream"], end="")
```

### Managing Networks & Volumes

Create and manage Docker networks and volumes using Resource objects.

```python
with DockerClient() as client:
    # Networks
    net = client.create_network("my-net", driver="bridge")
    print(net.id)
    net.remove()

    # Volumes
    vol = client.create_volume("my-data")
    print(vol.name)
    vol.remove()
```

### File Operations

Copy files and directories in and out of containers directly from the `Container` object.

```python
with DockerClient() as client:
    with client.ephemeral_container("alpine:latest", command="sleep 60") as container:
        # Copy host -> container
        container.copy_to("local_data/", "/dest/path/")

        # Copy container -> host
        container.copy_from("/src/path/data.txt", "local_output/")
```

### Real-world Example: Stirling-PDF Conversion

This example demonstrates running a Stirling-PDF container to convert a "Hello World" HTML file into a PDF and then retrieving it.

```python
from bollard import DockerClient
from time import sleep

env = {"SECURITY_ENABLE_LOGIN": "false"}

with DockerClient() as docker:
    with docker.container("frooodle/s-pdf", environment=env) as container:
        # Wait for the container to be ready
        res = ""
        while "HTTP" not in res:
            res = container.exec("curl -s -I http://localhost:8080")
            sleep(1)

        # Convert the HTML to PDF
        container.exec("sh -c 'echo \"<h1>Test PDF</h1>\" > /test.html'")
        result = container.exec(
            'curl -s -w "%{http_code}" '
            "-F 'fileInput=@/test.html' "
            "http://localhost:8080/api/v1/convert/html/pdf "
            "-o /test.pdf"
        )

        # Copy the PDF to the host
        container.copy_from("/test.pdf", ".")
```

### Kubernetes YAML Support

Execute Kubernetes YAML files directly using Podman's native `play kube` feature.

```python
with DockerClient() as client:
    # Requires a valid Kubernetes YAML file (Pod, Deployment, etc.)
    result = client.play_kube("pod.yaml")

    # Returns the JSON response from Podman describing created resources
    print("Created Pods:", result.get("Pods"))
```
