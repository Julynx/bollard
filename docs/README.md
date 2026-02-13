# Bollard

A Pythonic, zero-dependency client for the Docker and Podman Engine APIs.  
Prioritizes descriptive naming, context managers, and cross-platform ease of use.

## Key Features

- **Pythonic API**: `list_containers` instead of `ps`; `remove_image` instead of `rmi`.
- **Zero Dependencies**: Uses only the Python standard library (`http.client`, `socket`, `json`).
- **Smart Connection**: Auto-detects Docker/Podman sockets (Unix, Windows Pipes, `DOCKER_HOST`).
- **Windows Friendly**: Auto-starts the Podman machine on Windows if connection fails.
- **Resource Safety**: Context managers for client connections and ephemeral containers.
- **Streaming Output**: Real-time progress updates for long-running operations like pull and build.
- **Full Lifecycle**: Manage Containers, Images, Networks, and Volumes.

## Usage

### Basic Connection

```python
from bollard import DockerClient

# Auto-detects socket/pipe
with DockerClient() as client:
    for image in client.list_images():
        print(f"Image: {image.tags[0]}")
```

### Managing Containers

```python
with DockerClient() as client:
    # Run a container
    container = client.run_container("alpine:latest", command="echo 'Hello World'")
    
    # Get logs
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
    # Container is automatically removed here
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
