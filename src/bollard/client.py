"""
Client module. Provides functionality for the Docker/Podman Engine API.
"""

import contextlib
import http.client
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Generator, Literal, overload

from .const import DEFAULT_TIMEOUT
from .exceptions import DockerException
from .models import Container, Image, Network, Volume
from .transport import UnixHttpConnection

# Configure logging
logger = logging.getLogger(__name__)


class DockerClient:
    """A Pythonic client for the Docker/Podman Engine API.

    Supports:
        - Context Manager usage (`with DockerClient() as client:`)
        - Auto-starting Podman machine on Windows if connection fails
        - Ephemeral container contexts
    """

    def __init__(self, socket_path: str | None = None) -> None:
        if socket_path is None:
            if sys.platform == "win32":
                self.socket_path = self._discover_windows_pipe()
            else:
                self.socket_path = self._discover_linux_socket()
        else:
            self.socket_path = socket_path

        self._conn: UnixHttpConnection | None = None

    def _discover_windows_pipe(self) -> str:
        """Attempts to find a valid named pipe for Docker or Podman.

        Checks env vars DOCKER_HOST and DOCKER_SOCK first.
        If none are found, attempts to start the default Podman machine and retry.

        Returns:
            The path to the named pipe.
        """
        # 1. Check environment variables
        env_host = os.environ.get("DOCKER_HOST")
        if env_host and env_host.startswith("npipe://"):
            pipe = env_host.replace("npipe://", "")
            if self._check_pipe(pipe):
                logger.info("Connected via DOCKER_HOST: %s", pipe)
                return pipe

        env_sock = os.environ.get("DOCKER_SOCK")
        if env_sock and self._check_pipe(env_sock):
            logger.info("Connected via DOCKER_SOCK: %s", env_sock)
            return env_sock

        candidates = [
            r"\\.\pipe\docker_engine",
            r"\\.\pipe\podman-machine-default",
            r"\\.\pipe\podman-machine",
            r"\\.\pipe\docker_cli",
        ]

        # 2. Try to find an existing pipe
        for pipe in candidates:
            if self._check_pipe(pipe):
                logger.info("Connected to Docker/Podman via %s", pipe)
                return pipe

        # 3. If no pipe found, try starting Podman
        logger.info(
            "No active Docker/Podman pipe found. Attempting to start Podman machine..."
        )
        try:
            subprocess.run(
                ["podman", "machine", "start"], check=True, capture_output=True
            )
            logger.info("Podman machine started successfully. Retrying connection...")

            # Give it a moment to initialize the pipe
            for _ in range(5):
                time.sleep(1)
                for pipe in candidates:
                    if self._check_pipe(pipe):
                        logger.info("Connected to Docker/Podman via %s", pipe)
                        return pipe

        except (subprocess.CalledProcessError, FileNotFoundError):
            logger.warning("Failed to auto-start Podman machine.")

        # Default fallback if all else fails
        return candidates[0]

    def _discover_linux_socket(self) -> str:
        """Returns the location of the socket for the current user on Linux.

        Checks for DOCKER_HOST environment variable, then checks for podman
        and docker sockets.

        Returns:
            The path to the socket.
        """
        if os.environ.get("DOCKER_HOST"):
            return os.environ.get("DOCKER_HOST")
        podman_socket = Path(f"/run/user/{os.getuid()}/podman/podman.sock")
        if podman_socket.exists():
            # Check if the systemd daemon is running, if not, start it
            if (
                subprocess.run(
                    ["systemctl", "--user", "is-active", "podman.socket"],
                    check=False,
                    capture_output=True,
                ).returncode
                != 0
            ):
                subprocess.run(
                    ["systemctl", "--user", "enable", "--now", "podman.socket"],
                    check=True,
                    capture_output=True,
                )
            return str(podman_socket)
        docker_socket = Path("/run/docker.sock")
        return str(docker_socket)

    def _check_pipe(self, pipe: str) -> bool:
        try:
            with open(pipe, "r+b", buffering=0):
                return True
        except (FileNotFoundError, OSError):
            return False

    def __enter__(self) -> "DockerClient":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_traceback: Any) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def _get_connection(self) -> UnixHttpConnection:
        """Get or create a persistent connection."""
        if self._conn is None:
            self._conn = UnixHttpConnection(self.socket_path)
            self._conn.connect()
        return self._conn

    @contextlib.contextmanager
    def container(
        self, image: str, command: str | list[str] | None = None, **kwargs: Any
    ) -> Generator[Container, None, None]:
        """Context manager to run a container and ensure it is
        removed (or stopped) on exit.

        Usage:
            with client.container("alpine", "echo hello") as container:
                logs = container.logs()

        Args:
            image: Image to run.
            command: Command to run.
            **kwargs: Additional arguments passed to `run_container`.

        Yields:
            The running Container object.
        """
        container = self.run_container(image, command=command, **kwargs)
        try:
            yield container
        finally:
            try:
                # Use methods on the container object
                container.remove(force=True)
                logger.debug(
                    "Cleaned up ephemeral container %s", container.resource_id[:12]
                )
            except (OSError, DockerException) as e:
                logger.warning(
                    "Failed to cleanup container %s: %s", container.resource_id[:12], e
                )

    def _prepare_request_body(self, body: Any, headers: dict[str, str]) -> Any:
        if body and hasattr(body, "read"):
            try:
                current_pos = body.tell()
                body.seek(0, 2)
                end_pos = body.tell()
                body.seek(current_pos)
                headers["Content-Length"] = str(end_pos - current_pos)
            except (AttributeError, OSError):
                pass
            return body
        elif body:
            if isinstance(body, dict):
                json_body = json.dumps(body)
                headers["Content-Type"] = "application/json"
                headers["Content-Length"] = str(len(json_body))
                return json_body
            else:
                if isinstance(body, str):
                    headers["Content-Length"] = str(len(body))
                elif isinstance(body, bytes):
                    headers["Content-Length"] = str(len(body))
                return body
        return None

    def _request(
        self,
        method: str,
        endpoint: str,
        body: Any | None = None,
        headers: dict[str, str] | None = None,
        stream: bool = False,
    ) -> Any:
        """Helper to send HTTP requests to the socket"""
        conn = self._get_connection()
        headers = headers or {}

        body_to_send = self._prepare_request_body(body, headers)
        headers["Connection"] = "close"

        try:
            conn.request(method, endpoint, body=body_to_send, headers=headers)
            response = conn.getresponse()
        except (http.client.CannotSendRequest, BrokenPipeError, ConnectionError):
            logger.info("Connection lost, reconnecting...")
            self.close()
            conn = self._get_connection()
            conn.request(method, endpoint, body=body_to_send, headers=headers)
            response = conn.getresponse()

        if stream:
            if response.status >= 400:
                data = response.read().decode("utf-8")
                raise DockerException(f"Docker API Error ({response.status}): {data}")
            return response

        try:
            logger.debug(
                "Request: %s %s\nHeaders: %s",
                method,
                endpoint,
                headers,
            )

            data = response.read().decode("utf-8")

            if response.status < 400:
                if response.getheader("Content-Type") == "application/json":
                    return json.loads(data)
                try:
                    return json.loads(data)
                except json.JSONDecodeError:
                    return data

            raise DockerException(f"Docker API Error ({response.status}): {data}")
        except Exception:
            self.close()
            raise

    def _stream_json_response(
        self, response: http.client.HTTPResponse
    ) -> Generator[dict[str, Any], None, None]:
        """
        Helper to stream JSON objects from a Docker API response.
        Yields decoded JSON objects.
        """
        try:
            if response.status >= 400:
                data = response.read().decode("utf-8")
                raise DockerException(f"Docker API Error ({response.status}): {data}")

            # Iterate over lines for streaming responses
            while True:
                line = response.readline()
                if not line:
                    break

                line_str = line.decode("utf-8").strip()
                if not line_str:
                    continue

                try:
                    obj = json.loads(line_str)
                    if "error" in obj:
                        raise DockerException(f"Stream Error: {obj['error']}")
                    yield obj
                except json.JSONDecodeError:
                    logger.warning("Failed to decode JSON from stream: %s", line_str)
        finally:
            response.close()

    def list_containers(self, show_all: bool = False) -> list[Container]:
        """List containers.

        Equivalent to: `docker ps`

        Args:
            show_all: If True, show all containers (including stopped ones).

        Returns:
            A list of Container objects.
        """
        return Container.list(self, show_all=show_all)

    def run_container(
        self,
        image: str,
        command: str | list[str] | None = None,
        name: str | None = None,
        detach: bool = True,
        tty: bool = True,
        stdin_open: bool = True,
        environment: dict[str, str] | None = None,
        volumes: dict[str, str] | None = None,
        ports: dict[str, str | int] | None = None,
        runtime: str | None = None,
        gpu: bool = False,
        ipc_mode: str | None = None,
        **kwargs: Any,
    ) -> Container:
        """Create and start a container.

        Args:
            image: Image to run.
            command: Command to run.
            name: Name of the container.
            detach: If True, run container in background.
            tty: If True, allocate a pseudo-TTY.
            stdin_open: If True, keep STDIN open even if not attached.
            environment: Environment variables.
            volumes: Volume mappings.
            ports: Port mappings.
            runtime: Runtime to use.
            gpu: If True, enable GPU support.
            ipc_mode: IPC mode.
            **kwargs: Additional arguments passed to container creation.

        Returns:
            The started Container object.
        """
        return Container.run(
            self,
            image,
            command=command,
            name=name,
            detach=detach,
            tty=tty,
            stdin_open=stdin_open,
            environment=environment,
            volumes=volumes,
            ports=ports,
            runtime=runtime,
            gpu=gpu,
            ipc_mode=ipc_mode,
            **kwargs,
        )

    def stop_container(self, container_id: str, timeout: int = DEFAULT_TIMEOUT) -> Any:
        """Stop a container.

        Args:
            container_id: ID or name of the container.
            timeout: Timeout in seconds to wait for the container
            to stop before killing it.

        Returns:
            Response from the Docker API.
        """
        return Container(self, {"Id": container_id}).stop(timeout=timeout)

    def kill_container(self, container_id: str, signal: str = "SIGKILL") -> Any:
        """Kill a container.

        Args:
            container_id: ID or name of the container.
            signal: Signal to send to the container.

        Returns:
            Response from the Docker API.
        """
        return Container(self, {"Id": container_id}).kill(signal=signal)

    @overload
    def pull_image(
        self, image_name: str, progress: Literal[True]
    ) -> Generator[dict[str, Any], None, None]: ...

    @overload
    def pull_image(
        self, image_name: str, progress: Literal[False] = False
    ) -> Image: ...

    def pull_image(
        self, image_name: str, progress: bool = False
    ) -> Generator[dict[str, Any], None, None] | Image:
        """Pull an image.

        Args:
            image_name: Name of the image to pull.
            progress: If True, returns a generator illustrating progress.

        Returns:
            The pulled Image object, or a generator if progress is True.
        """
        return Image.pull(self, image_name, progress=progress)

    def list_images(self, show_all: bool = False) -> list[Image]:
        """List images.

        Args:
            show_all: If True, show all images.

        Returns:
            A list of Image objects.
        """
        return Image.list(self, show_all=show_all)

    @overload
    def build_image(
        self, path: str, tag: str, progress: Literal[True]
    ) -> Generator[dict[str, Any], None, None]: ...

    @overload
    def build_image(
        self, path: str, tag: str, progress: Literal[False] = False
    ) -> Image: ...

    def build_image(
        self, path: str, tag: str, progress: bool = False
    ) -> Generator[dict[str, Any], None, None] | Image:
        """Build an image from a directory.

        Args:
            path: Path to the directory containing the Dockerfile.
            tag: Tag to apply to the built image.
            progress: If True, returns a generator illustrating progress.

        Returns:
            The built Image object, or a generator if progress is True.
        """
        return Image.build(self, path, tag, progress=progress)

    def remove_image(self, image: str, force: bool = False) -> Any:
        """Remove an image.

        Args:
            image: ID or name of the image.
            force: If True, force removal of the image.

        Returns:
            Response from the Docker API.
        """
        return Image(self, {"Id": image}).remove(force=force)

    def remove_container(
        self,
        container_id: str,
        force: bool = False,
        remove_links: bool = False,
        remove_volumes: bool = False,
    ) -> Any:
        """Remove a container.

        Args:
            container_id: ID or name of the container.
            force: If True, force removal of the container.
            remove_links: If True, remove the specified link and
            not the underlying container.
            remove_volumes: If True, remove the volumes associated with the container.

        Returns:
            Response from the Docker API.
        """
        return Container(self, {"Id": container_id}).remove(
            force=force, remove_links=remove_links, remove_volumes=remove_volumes
        )

    def start_container(self, container_id: str) -> Any:
        """Start a container.

        Args:
            container_id: ID or name of the container.

        Returns:
            Response from the Docker API.
        """
        return Container(self, {"Id": container_id}).start()

    def restart_container(
        self, container_id: str, timeout: int = DEFAULT_TIMEOUT
    ) -> None:
        """Restart a container.

        Args:
            container_id: ID or name of the container.
            timeout: Timeout in seconds to wait for the container
            to stop before restarting.
        """
        return Container(self, {"Id": container_id}).restart(timeout=timeout)

    def inspect_container(self, container_id: str) -> dict[str, Any]:
        """Inspect a container.

        Args:
            container_id: ID or name of the container.

        Returns:
            A dictionary containing container attributes.
        """
        # Reload fetches inspect data
        container = Container(self, {"Id": container_id})
        container.reload()
        return container.attrs

    def play_kube(self, path: str) -> dict[str, Any]:
        """Play a Kubernetes YAML file using Podman.

        Equivalent to: `podman play kube <path>`

        Args:
            path: Path to the Kubernetes YAML file.

        Returns:
            Response from the Podman API.

        Raises:
            FileNotFoundError: If the YAML file does not exist.
        """
        if not os.path.exists(path):
            raise FileNotFoundError(f"YAML file not found: {path}")

        with open(path, "r", encoding="utf-8") as file_object:
            content = file_object.read()

        payload = {"K8sYAML": content}
        return self._request("POST", "/libpod/play/kube", body=payload)

    def get_container_logs(self, container_id: str, tail: str | int = "all") -> str:
        """Fetch container logs.

        Args:
            container_id: ID or name of the container.
            tail: Number of lines to show from the end of the logs.

        Returns:
            The container logs as a string.
        """
        return Container(self, {"Id": container_id}).logs(tail=tail)

    def execute_command(
        self,
        container_id: str,
        command: str | list[str],
        detach: bool = False,
        tty: bool = False,
    ) -> str:
        """Execute a command in a running container.

        Args:
            container_id: ID or name of the container.
            command: Command to execute.
            detach: If True, run command in background.
            tty: If True, allocate a pseudo-TTY.

        Returns:
            The output of the command (if not detached) or the exec ID.
        """
        return Container(self, {"Id": container_id}).exec(
            command, detach=detach, tty=tty
        )

    def list_networks(self) -> list[Network]:
        """List networks.

        Returns:
            A list of Network objects.
        """
        return Network.list(self)

    def create_network(
        self, name: str, driver: str = "bridge", **kwargs: Any
    ) -> Network:
        """Create a network.

        Args:
            name: Name of the network.
            driver: Network driver to use.
            **kwargs: Additional arguments passed to network creation.

        Returns:
            The created Network object.
        """
        return Network.create(self, name, driver=driver, **kwargs)

    def remove_network(self, network_id: str) -> None:
        """Remove a network.

        Args:
            network_id: ID or name of the network.
        """
        return Network(self, {"Id": network_id}).remove()

    def inspect_network(self, network_id: str) -> Any:
        """Inspect a network.

        Args:
            network_id: ID or name of the network.

        Returns:
            A dictionary containing network attributes.
        """
        return Network(self, {"Id": network_id}).inspect()

    def list_volumes(self) -> list[Volume]:
        """List volumes.

        Returns:
            A list of Volume objects.
        """
        return Volume.list(self)

    def create_volume(self, name: str, driver: str = "local", **kwargs: Any) -> Volume:
        """Create a volume.

        Args:
            name: Name of the volume.
            driver: Volume driver to use.
            **kwargs: Additional arguments passed to volume creation.

        Returns:
            The created Volume object.
        """
        return Volume.create(self, name, driver=driver, **kwargs)

    def remove_volume(self, name: str, force: bool = False) -> None:
        """Remove a volume.

        Args:
            name: Name of the volume.
            force: If True, force removal of the volume.
        """
        return Volume(self, {"Name": name}).remove(force=force)

    def inspect_volume(self, name: str) -> Any:
        """Inspect a volume.

        Args:
            name: Name of the volume.

        Returns:
            A dictionary containing volume attributes.
        """
        return Volume(self, {"Name": name}).inspect()

    def put_archive(self, container_id: str, path: str, data: bytes) -> None:
        """Upload a tar archive to a container.

        Args:
            container_id: ID or name of the container.
            path: Path in the container to extract the archive to.
            data: The tar archive data as bytes.
        """
        Container(self, {"Id": container_id}).put_archive(path, data)

    def get_archive(self, container_id: str, path: str) -> tuple[bytes, dict[str, Any]]:
        """Download a tar archive from a container.

        Args:
            container_id: ID or name of the container.
            path: Path in the container to download.

        Returns:
            A tuple containing the archive data (bytes) and
            a dictionary of file statistics.
        """
        return Container(self, {"Id": container_id}).get_archive(path)

    def copy_to_container(
        self, container_id: str, source_path: str, destination_path: str
    ) -> None:
        """Copy a local file or directory into a container.

        Args:
            container_id: ID or name of the container.
            source_path: Local path to the file or directory.
            destination_path: Path in the container.
        """
        Container(self, {"Id": container_id}).copy_to(source_path, destination_path)

    def copy_from_container(
        self, container_id: str, source_path: str, destination_path: str
    ) -> None:
        """Copy a file or directory from a container to the local filesystem.

        Args:
            container_id: ID or name of the container.
            source_path: Path in the container to copy from.
            destination_path: Local path to copy to.
        """
        Container(self, {"Id": container_id}).copy_from(source_path, destination_path)

    def load_docker_config(self) -> dict[str, Any]:
        """Load Docker configuration from default locations or DOCKER_CONFIG.

        Returns:
            The 'auths' section of the config, or an empty dictionary if not found.
        """
        config_path = os.environ.get("DOCKER_CONFIG")
        if not config_path:
            home = os.path.expanduser("~")
            config_path = os.path.join(home, ".docker", "config.json")

        if not os.path.exists(config_path):
            return {}

        try:
            with open(config_path, "r", encoding="utf-8") as config_file:
                data = json.load(config_file)
                auths: dict[str, Any] = data.get("auths", {})
                return auths

        except (OSError, json.JSONDecodeError):
            logger.warning("Failed to load Docker config from %s", config_path)
            return {}

    @overload
    def push_image(
        self,
        image: str,
        tag: str | None = None,
        auth_config: dict[str, str] | None = None,
        progress: Literal[True] = ...,
    ) -> Generator[dict[str, Any], None, None]: ...

    @overload
    def push_image(
        self,
        image: str,
        tag: str | None = None,
        auth_config: dict[str, str] | None = None,
        progress: Literal[False] = False,
    ) -> list[dict[str, Any]]: ...

    def push_image(
        self,
        image: str,
        tag: str | None = None,
        auth_config: dict[str, str] | None = None,
        progress: bool = False,
    ) -> Generator[dict[str, Any], None, None] | list[dict[str, Any]]:
        """Push an image.

        Args:
            image: Name of the image to push.
            tag: Optional tag to push.
            auth_config: Optional authentication configuration.
            progress: If True, returns a generator illustrating progress.

        Returns:
            A list of events (if progress is False) or
            a generator (if progress is True).
        """
        return Image(self, {"Id": image}).push(
            tag=tag, auth_config=auth_config, progress=progress
        )
