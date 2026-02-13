import contextlib
import http.client
import json
import logging
import os
import subprocess
import sys
import time
from typing import Any, Generator

from .const import DEFAULT_TIMEOUT
from .exceptions import DockerException
from .models import Container, Image, Network, Volume
from .transport import UnixHttpConnection

# Configure logging to INFO
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
handler = logging.StreamHandler(sys.stdout)
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
handler.setFormatter(formatter)
logger.addHandler(handler)


class DockerClient:
    """
    A Pythonic client for the Docker/Podman Engine API.

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
                self.socket_path = "/var/run/docker.sock"
        else:
            self.socket_path = socket_path

    def _discover_windows_pipe(self) -> str:
        """
        Attempts to find a valid named pipe for Docker or Podman.
        Checks env vars DOCKER_HOST and DOCKER_SOCK first.
        If none are found, attempts to start the default Podman machine and retry.
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

    def _check_pipe(self, pipe: str) -> bool:
        try:
            with open(pipe, "r+b", buffering=0):
                return True
        except (FileNotFoundError, OSError):
            return False

    def __enter__(self) -> "DockerClient":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_traceback: Any) -> None:
        pass

    @contextlib.contextmanager
    def container(
        self, image: str, command: str | list[str] | None = None, **kwargs: Any
    ) -> Generator[Container, None, None]:
        """
        Context manager to run a container and ensure it is removed (or stopped) on exit.

        Usage:
            with client.container("alpine", "echo hello") as container:
                print(container.logs())
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

    def _request(
        self,
        method: str,
        endpoint: str,
        body: Any | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """Helper to send HTTP requests to the socket"""
        conn = UnixHttpConnection(self.socket_path)
        headers = headers or {}

        if body:
            if isinstance(body, dict):
                json_body = json.dumps(body)
                headers["Content-Type"] = "application/json"
                headers["Content-Length"] = str(len(json_body))
            else:
                # Raw body (bytes/str)
                json_body = body
        else:
            json_body = None

        headers["Connection"] = "close"

        try:
            logger.debug("%s %s", method, endpoint)
            conn.request(method, endpoint, body=json_body, headers=headers)
            response = conn.getresponse()
            data = response.read().decode("utf-8")

            if response.status < 400:
                if response.getheader("Content-Type") == "application/json":
                    return json.loads(data)
                try:
                    return json.loads(data)
                except json.JSONDecodeError:
                    return data

            raise DockerException(f"Docker API Error ({response.status}): {data}")
        finally:
            conn.close()

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
        """
        List containers.
        Equivalent to: docker ps
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
        """
        Create and start a container.
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
        """
        Stop a container.
        """
        return Container(self, {"Id": container_id}).stop(timeout=timeout)

    def kill_container(self, container_id: str, signal: str = "SIGKILL") -> Any:
        """
        Kill a container.
        """
        return Container(self, {"Id": container_id}).kill(signal=signal)

    def pull_image(self, image_name: str) -> Generator[dict[str, Any], None, None]:
        """
        Pull an image.
        """
        yield from Image.pull(self, image_name)

    def list_images(self, show_all: bool = False) -> list[Image]:
        """
        List images.
        """
        return Image.list(self, show_all=show_all)

    def build_image(self, path: str, tag: str) -> Generator[dict[str, Any], None, None]:
        """
        Build an image from a directory.
        """
        yield from Image.build(self, path, tag)

    def remove_image(self, image: str, force: bool = False) -> Any:
        """
        Remove an image.
        """
        return Image(self, {"Id": image}).remove(force=force)

    def remove_container(
        self,
        container_id: str,
        force: bool = False,
        remove_links: bool = False,
        remove_volumes: bool = False,
    ) -> Any:
        """
        Remove a container.
        """
        return Container(self, {"Id": container_id}).remove(
            force=force, remove_links=remove_links, remove_volumes=remove_volumes
        )

    def start_container(self, container_id: str) -> Any:
        """
        Start a container.
        """
        return Container(self, {"Id": container_id}).start()

    def restart_container(
        self, container_id: str, timeout: int = DEFAULT_TIMEOUT
    ) -> None:
        """
        Restart a container.
        """
        return Container(self, {"Id": container_id}).restart(timeout=timeout)

    def inspect_container(self, container_id: str) -> dict[str, Any]:
        """
        Inspect a container.
        """
        # Reload fetches inspect data
        container = Container(self, {"Id": container_id})
        container.reload()
        return container.attrs

    def play_kube(self, path: str) -> dict[str, Any]:
        """
        Play a Kubernetes YAML file using Podman.
        Equivalent to: podman play kube <path>
        """
        if not os.path.exists(path):
            raise FileNotFoundError(f"YAML file not found: {path}")

        with open(path, "r", encoding="utf-8") as file_object:
            content = file_object.read()

        payload = {"K8sYAML": content}
        return self._request("POST", "/libpod/play/kube", body=payload)

    def get_container_logs(self, container_id: str, tail: str | int = "all") -> str:
        """
        Fetch container logs.
        """
        return Container(self, {"Id": container_id}).logs(tail=tail)

    def execute_command(
        self,
        container_id: str,
        command: str | list[str],
        detach: bool = False,
        tty: bool = False,
    ) -> str:
        """
        Execute a command in a running container.
        """
        return Container(self, {"Id": container_id}).exec(
            command, detach=detach, tty=tty
        )

    def list_networks(self) -> list[Network]:
        """
        List networks.
        """
        return Network.list(self)

    def create_network(
        self, name: str, driver: str = "bridge", **kwargs: Any
    ) -> Network:
        """
        Create a network.
        """
        return Network.create(self, name, driver=driver, **kwargs)

    def remove_network(self, network_id: str) -> None:
        """
        Remove a network.
        """
        return Network(self, {"Id": network_id}).remove()

    def inspect_network(self, network_id: str) -> Any:
        """
        Inspect a network.
        """
        return Network(self, {"Id": network_id}).inspect()

    def list_volumes(self) -> list[Volume]:
        """
        List volumes.
        """
        return Volume.list(self)

    def create_volume(self, name: str, driver: str = "local", **kwargs: Any) -> Volume:
        """
        Create a volume.
        """
        return Volume.create(self, name, driver=driver, **kwargs)

    def remove_volume(self, name: str, force: bool = False) -> None:
        """
        Remove a volume.
        """
        return Volume(self, {"Name": name}).remove(force=force)

    def inspect_volume(self, name: str) -> Any:
        """
        Inspect a volume.
        """
        return Volume(self, {"Name": name}).inspect()

    def put_archive(self, container_id: str, path: str, data: bytes) -> None:
        """
        Upload a tar archive to a container.
        """
        Container(self, {"Id": container_id}).put_archive(path, data)

    def get_archive(self, container_id: str, path: str) -> tuple[bytes, dict[str, Any]]:
        """
        Download a tar archive from a container.
        """
        return Container(self, {"Id": container_id}).get_archive(path)

    def copy_to_container(
        self, container_id: str, source_path: str, destination_path: str
    ) -> None:
        """
        Copy a local file or directory into a container.
        """
        Container(self, {"Id": container_id}).copy_to(source_path, destination_path)

    def copy_from_container(
        self, container_id: str, source_path: str, destination_path: str
    ) -> None:
        """
        Copy a file or directory from a container to the local filesystem.
        """
        Container(self, {"Id": container_id}).copy_from(source_path, destination_path)

    def load_docker_config(self) -> dict[str, Any]:
        """
        Load Docker configuration from default locations or DOCKER_CONFIG.
        Returns the 'auths' section of the config.
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

    def push_image(
        self,
        image: str,
        tag: str | None = None,
        auth_config: dict[str, str] | None = None,
    ) -> Generator[dict[str, Any], None, None]:
        """
        Push an image.
        """
        return Image(self, {"Id": image}).push(tag=tag, auth_config=auth_config)
