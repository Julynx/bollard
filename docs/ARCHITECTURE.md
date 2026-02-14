# Bollard Architecture

This document provides a high-level technical overview of the `bollard` library, a zero-dependency Python client for Docker and Podman.

## Design Philosophy

1. **Zero Dependencies**: The library relies **only** on the Python standard library (`http.client`, `socket`, `json`, `ctypes` for Windows pipes). This ensures easy installation and minimal supply chain risk.
2. **Pythonic Interface**: Wraps the raw HTTP API in idiomatic Python classes (`Container`, `Image`) with context managers and generators, rather than a 1:1 mapping of CLI commands.
3. **Cross-Platform Native**: First-class support for Windows Named Pipes (`npipe://`) and Unix Sockets (`unix://`), including automatic discovery and Podman machine starting.

## Directory Structure

```text
src/bollard/
├── client.py          # Entry point. Manages connection and high-level operations.
├── transport.py       # Custom HTTPConnection and NpipeSocket implementation.
├── container.py       # Container resource model (run, stop, exec, etc.).
├── image.py           # Image resource model (pull, build, push).
├── network.py         # Network resource model.
├── volume.py          # Volume resource model.
├── models.py          # Export facade for resource classes.
├── exceptions.py      # Custom exception hierarchy.
├── progress.py        # Progress reporting for streaming operations.
├── ignore.py          # .dockerignore parsing and matching.
└── const.py           # Project-wide constants.
```

## Core Components

### 1. DockerClient (`client.py`)

The central coordinator. It is **stateless** regarding resources but **stateful** regarding the connection configuration.

- **Responsibilities**:
  - Socket/Pipe discovery (environment variables, default paths).
  - Parametrizing HTTP requests (`_request`).
  - Factory for resource objects (`containers()`, `images()`).
- **Context Manager**: `__enter__` returns self, ensuring cleanup if we add stateful connections later.

### 2. Transport Layer (`transport.py`)

The "magic" that makes standard `http.client` talk to non-TCP sockets.

- **`UnixHttpConnection`**: Subclasses `http.client.HTTPConnection`. Overrides `connect()` to use `socket.AF_UNIX` or `NpipeSocket`.
- **`NpipeSocket`**: A pure-Python wrapper around Windows Named Pipes APIs (`os.open`, `os.read`, `os.write`) that mimics the `socket` interface (`recv`, `sendall`).

### 3. Resource Models (`container.py`, etc.)

Classes representing Docker entities.

- **Structure**: Inherit from `DockerResource`. Hold a reference to the `client` and the resource's raw attributes (`attrs`).
- **Behavior**: Methods like `stop()`, `remove()` translate directly to API calls (e.g., `POST /containers/{id}/stop`).
- **Lazy Loading**: Attributes are populated from the initial list/inspect call. `reload()` fetches fresh data.

### 4. Progress Reporting (`progress.py`)

Handles real-time progress updates from streaming operations (e.g., `pull`, `push`, `build`).

- **DockerProgress**: Consumes a generator of JSON events from the Docker API.
- **Output**: Logs progress messages to the standard logger (`logging.INFO`).
- **Layer Tracking**: Tracks status of individual image layers during pull/push operations to avoidspamming the logs.

### 5. Context Parsing (`ignore.py`)

Implements `.dockerignore` support to exclude files from build contexts.

- **DockerIgnore**: Parses `.dockerignore` files and provides `is_ignored(path)` method.
- **Matching**: Uses `fnmatch` to approximate Docker's Go-based matching logic, handling negations (`!`) and wildcards.

## Data Flow

When a user executes `client.list_containers()`:

1. **Client**: Constructs HTTP request `GET /containers/json`.
2. **Transport**: `UnixHttpConnection` opens a connection to the socket/pipe (e.g., `/var/run/docker.sock` or `\\.\pipe\docker_engine`).
3. **Socket**: Sends raw HTTP bytes over the IPC channel.
4. **Engine**: Docker/Podman Daemon receives request, processes it, returns JSON.
5. **Client**: Decodes JSON body.
6. **Model**: Instantiates `Container` list with the returned data.

## Key Implementation Details

### Windows Named Pipe Discovery

The client iterates through a list of candidate pipes (`docker_engine`, `podman-machine-default`) and keeps the first one that opens successfully. If none are found, it attempts to run `podman machine start`.

### Streaming Responses

Operations like `pull_image` return Python **Generators**. The client reads the HTTP response line-by-line, decoding JSON objects as they arrive, enabling real-time progress bars without buffering the entire response.

### Ephemeral Containers

The `client.container()` context manager ensures cleanup:

1. **Enter**: `run_container()` creates and starts the container.
2. **Yield**: Returns the `Container` object.
3. **Exit**: Calls `container.remove(force=True)`, swallowing errors if the container is already gone.

## Testing Strategy (`tests/`)

- **Integration Tests**: The primary test suite (`tests/`) requires a running Docker/Podman engine. It spins up real containers to verify behavior.
- **Fixtures**: `docker_client` fixture handles connection setup/teardown for each test.
