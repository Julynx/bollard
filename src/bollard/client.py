import base64
import contextlib
import http.client
import io
import json
import logging
import os
import shlex
import subprocess
import sys
import tarfile
import time
import urllib.parse
from typing import Any, Generator

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

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
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
                logger.debug("Cleaned up ephemeral container %s", container.id[:12])
            except (OSError, DockerException) as e:
                logger.warning(
                    "Failed to cleanup container %s: %s", container.id[:12], e
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
        API: GET /containers/json
        """
        params = {"all": "true"} if show_all else {}
        query = urllib.parse.urlencode(params)
        data = self._request("GET", f"/containers/json?{query}")
        return [Container(self, c) for c in data]

    def run_container(
        self,
        image: str,
        command: str | list[str] | None = None,
        name: str | None = None,
        # Default behavior: Keep stdin open and allocate a pseudo-TTY.
        # This prevents shell containers (alpine, ubuntu) from exiting immediately.
        detach: bool = True,
        tty: bool = True,
        stdin_open: bool = True,
        environment: dict[str, str] | None = None,
        volumes: dict[str, str]
        | None = None,  # {host_path: container_path} or vice versa? Docker API usually uses "host:container:mode"
        ports: dict[str, str | int] | None = None,  # {container_port: host_port}
        runtime: str | None = None,
        gpu: bool = False,
        ipc_mode: str | None = None,
        **kwargs: Any,
    ) -> Container:
        """
        Create and start a container.
        Defaults to -itd (Interactive, TTY, Detached) behavior.

        Args:
            image: Image to use
            command: Command to run
            name: Optional name for the container
            detach: Whether to run in background
            tty: Allocate a pseudo-TTY
            stdin_open: Keep stdin open
            environment: Dictionary of environment variables
            volumes: Dictionary of volume mappings {host_path: container_path}
            ports: Dictionary of port mappings {container_port: host_port}
            runtime: Runtime to use (e.g. "nvidia")
            gpu: If True, adds NVIDIA GPU device requests
            ipc_mode: IPC mode to use (e.g. "host")
            **kwargs: Additional parameters for the container creation API
        """
        logger.info("Creating container for image '%s'...", image)

        payload: dict[str, Any] = {
            "Image": image,
            "Tty": tty,
            "OpenStdin": stdin_open,
        }

        if command:
            if isinstance(command, str):
                payload["Cmd"] = shlex.split(command)
            else:
                payload["Cmd"] = command

        if environment:
            payload["Env"] = [f"{k}={v}" for k, v in environment.items()]

        host_config: dict[str, Any] = {}

        if volumes:
            # Docker API Format for Binds is ["/host:/container:ro"]
            host_config["Binds"] = [f"{h}:{c}" for h, c in volumes.items()]

        if ports:
            # "ExposedPorts": { "80/tcp": {} }
            # "HostConfig": { "PortBindings": { "80/tcp": [{ "HostPort": "8080" }] } }
            exposed_ports = {}
            port_bindings = {}
            for container_port, host_port in ports.items():
                if "/" not in str(container_port):
                    container_port = f"{container_port}/tcp"
                exposed_ports[container_port] = {}
                port_bindings[container_port] = [{"HostPort": str(host_port)}]
            payload["ExposedPorts"] = exposed_ports
            host_config["PortBindings"] = port_bindings

        if runtime:
            host_config["Runtime"] = runtime

        if ipc_mode:
            host_config["IpcMode"] = ipc_mode

        if gpu:
            # Simplified NVIDIA GPU request
            host_config["DeviceRequests"] = [
                {
                    "Driver": "",
                    "Count": -1,  # All GPUs
                    "DeviceIDs": [],
                    "Capabilities": [["gpu"]],
                    "Options": {},
                }
            ]

        if host_config:
            payload["HostConfig"] = host_config

        # Merging extra kwargs into payload.
        # If kwargs contains HostConfig, we might need a deep merge, but for now simple update.
        if "HostConfig" in kwargs and host_config:
            payload["HostConfig"].update(kwargs.pop("HostConfig"))

        payload.update(kwargs)

        endpoint = "/containers/create"
        if name:
            endpoint += f"?name={name}"

        try:
            create_res = self._request("POST", endpoint, body=payload)
        except DockerException as e:
            if "404" in str(e):
                logger.info("Image '%s' not found, pulling...", image)
                for _ in self.pull_image(image):
                    pass
                logger.info("Image pulled successfully. Retrying container creation...")
                create_res = self._request("POST", endpoint, body=payload)
            else:
                raise e

        container_id: str = create_res["Id"]

        try:
            logger.info("Starting container %s...", container_id[:12])
            self._request("POST", f"/containers/{container_id}/start")
        except Exception as e:
            logger.error(
                "Failed to start container %s: %s. Cleaning up...", container_id[:12], e
            )
            try:
                self.remove_container(container_id, force=True)
            except Exception:
                pass
            raise e

        return Container(
            self,
            {
                "Id": container_id,
                "Image": image,
                "Names": [f"/{name}"] if name else [],
            },
        )

    def stop_container(self, container_id: str, timeout: int = 10) -> Any:
        """
        Stop a container.
        Equivalent to: docker stop
        """
        logger.info("Stopping container %s...", container_id[:12])
        return self._request("POST", f"/containers/{container_id}/stop?t={timeout}")

    def pull_image(self, image_name: str) -> Generator[dict[str, Any], None, None]:
        """
        Pull an image.
        Equivalent to: docker pull

        Yields:
            dict: Progress objects from Docker
        """
        logger.info("Pulling %s...", image_name)

        conn = UnixHttpConnection(self.socket_path)
        # Note: We manually handle the connection here to use our stream helper
        # Instead of self._request which reads the whole body
        conn.request("POST", f"/images/create?fromImage={image_name}")
        response = conn.getresponse()

        yield from self._stream_json_response(response)

    def list_images(self, show_all: bool = False) -> list[Image]:
        """
        List images.
        Equivalent to: docker images
        """
        params = {"all": "true"} if show_all else {}
        query = urllib.parse.urlencode(params)
        data = self._request("GET", f"/images/json?{query}")
        return [Image(self, i) for i in data]

    def build_image(self, path: str, tag: str) -> Generator[dict[str, Any], None, None]:
        """
        Build an image from a directory.
        Equivalent to: docker build -t tag path

        Yields:
            dict: Build progress objects
        """
        logger.info("Building image %s from %s...", tag, path)

        # Create a tar context
        tar_stream = io.BytesIO()
        with tarfile.open(fileobj=tar_stream, mode="w") as tar:
            tar.add(path, arcname=".")
        tar_stream.seek(0)

        query = urllib.parse.urlencode({"t": tag})
        headers = {
            "Content-Type": "application/x-tar",
            "Content-Length": str(len(tar_stream.getvalue())),
            "Connection": "close",
        }

        conn = UnixHttpConnection(self.socket_path)
        logger.debug("POST /build?%s", query)
        conn.request(
            "POST", f"/build?{query}", body=tar_stream.getvalue(), headers=headers
        )
        response = conn.getresponse()

        yield from self._stream_json_response(response)

    def remove_image(self, image: str, force: bool = False) -> Any:
        """
        Remove an image.
        Equivalent to: docker rmi
        """
        logger.info("Removing image %s...", image)
        params = {"force": "true"} if force else {}
        query = urllib.parse.urlencode(params)
        return self._request("DELETE", f"/images/{image}?{query}")

    def remove_container(
        self,
        container_id: str,
        force: bool = False,
        remove_links: bool = False,
        remove_volumes: bool = False,
    ) -> Any:
        """
        Remove a container.
        Equivalent to: docker rm
        """
        logger.info("Removing container %s...", container_id[:12])
        params = {}
        if force:
            params["force"] = "true"
        if remove_links:
            params["link"] = "true"
        if remove_volumes:
            params["v"] = "true"

        query = urllib.parse.urlencode(params)
        return self._request("DELETE", f"/containers/{container_id}?{query}")

    def start_container(self, container_id: str) -> Any:
        """
        Start a container.
        Equivalent to: docker start
        """
        logger.info("Starting container %s...", container_id[:12])
        return self._request("POST", f"/containers/{container_id}/start")

    def restart_container(self, container_id: str, timeout: int = 10) -> Any:
        """
        Restart a container.
        Equivalent to: docker restart
        """
        logger.info("Restarting container %s...", container_id[:12])
        return self._request("POST", f"/containers/{container_id}/restart?t={timeout}")

    def get_container_logs(self, container_id: str, tail: str | int = "all") -> str:
        """
        Fetch container logs.
        Equivalent to: docker logs
        """
        params = {"stdout": "true", "stderr": "true", "tail": str(tail)}
        query = urllib.parse.urlencode(params)
        conn = UnixHttpConnection(self.socket_path)
        try:
            logger.debug("GET /containers/%s/logs?%s", container_id, query)
            conn.request("GET", f"/containers/{container_id}/logs?{query}")
            response = conn.getresponse()
            data = response.read()
            return data.decode("utf-8", errors="ignore")
        finally:
            conn.close()

    def _read_multiplexed_response(self, response: http.client.HTTPResponse) -> str:
        """
        Reads a Docker multiplexed response (when Tty=False) and combines stdout/stderr.
        Header format: [SOCKET_TYPE (1 byte), 0, 0, 0, SIZE (4 bytes big endian)]
        SOCKET_TYPE: 1=stdout, 2=stderr
        """
        output = []
        while True:
            header = response.read(8)
            if not header:
                break
            if len(header) < 8:
                # Malformed or truncated header
                break

            stream_type = header[0]
            payload_size = int.from_bytes(header[4:8], "big")

            payload = response.read(payload_size)
            if stream_type in (1, 2):  # stdout or stderr
                output.append(payload.decode("utf-8", errors="ignore"))

        return "".join(output)

    def execute_command(
        self,
        container_id: str,
        command: str | list[str],
        detach: bool = False,
        tty: bool = False,
    ) -> str:
        """
        Execute a command in a running container.
        Equivalent to: docker exec
        """
        # 1. Create
        payload: dict[str, Any] = {
            "AttachStdin": False,
            "AttachStdout": True,
            "AttachStderr": True,
            "Tty": tty,
            "Cmd": command if isinstance(command, list) else shlex.split(command),
        }
        res: dict[str, Any] = self._request(
            "POST", f"/containers/{container_id}/exec", body=payload
        )
        exec_id: str = res["Id"]

        # 2. Start
        start_payload = {"Detach": detach, "Tty": tty}

        if detach:
            self._request("POST", f"/exec/{exec_id}/start", body=start_payload)
            return exec_id

        conn = UnixHttpConnection(self.socket_path)
        headers = {"Content-Type": "application/json"}
        try:
            logger.debug("POST /exec/%s/start", exec_id)
            conn.request(
                "POST",
                f"/exec/{exec_id}/start",
                body=json.dumps(start_payload),
                headers=headers,
            )
            response = conn.getresponse()

            if response.status >= 400:
                data = response.read().decode("utf-8", errors="ignore")
                raise DockerException(f"Exec Error ({response.status}): {data}")

            if tty:
                # Raw stream
                return response.read().decode("utf-8", errors="ignore")
            else:
                # Multiplexed stream
                return self._read_multiplexed_response(response)
        finally:
            conn.close()

    def list_networks(self) -> list[Network]:
        """
        List networks.
        Equivalent to: docker network ls
        """
        data = self._request("GET", "/networks")
        return [Network(self, n) for n in data]

    def create_network(
        self, name: str, driver: str = "bridge", **kwargs: Any
    ) -> Network:
        """
        Create a network.
        Equivalent to: docker network create
        """
        logger.info("Creating network %s (driver=%s)...", name, driver)
        payload = {"Name": name, "Driver": driver, **kwargs}
        res = self._request("POST", "/networks/create", body=payload)
        # res has only Id and Warning.
        return Network(self, {"Id": res.get("Id"), "Name": name, "Driver": driver})

    def remove_network(self, network_id: str) -> None:
        """
        Remove a network.
        Equivalent to: docker network rm
        """
        logger.info("Removing network %s...", network_id)
        self._request("DELETE", f"/networks/{network_id}")

    def inspect_network(self, network_id: str) -> Any:
        """
        Inspect a network.
        Equivalent to: docker network inspect
        """
        return self._request("GET", f"/networks/{network_id}")

    def list_volumes(self) -> list[Volume]:
        """
        List volumes.
        Equivalent to: docker volume ls
        """
        data = self._request("GET", "/volumes")
        volumes = data.get("Volumes") or []
        return [Volume(self, v) for v in volumes]

    def create_volume(self, name: str, driver: str = "local", **kwargs: Any) -> Volume:
        """
        Create a volume.
        Equivalent to: docker volume create
        """
        logger.info("Creating volume %s (driver=%s)...", name, driver)
        payload = {"Name": name, "Driver": driver, **kwargs}
        res = self._request("POST", "/volumes/create", body=payload)
        return Volume(self, res)

    def remove_volume(self, name: str, force: bool = False) -> None:
        """
        Remove a volume.
        Equivalent to: docker volume rm
        """
        logger.info("Removing volume %s...", name)
        query = urllib.parse.urlencode({"force": "true"} if force else {})
        self._request("DELETE", f"/volumes/{name}?{query}")

    def inspect_volume(self, name: str) -> Any:
        """
        Inspect a volume.
        Equivalent to: docker volume inspect
        """
        return self._request("GET", f"/volumes/{name}")

    def put_archive(self, container_id: str, path: str, data: bytes) -> None:
        """
        Upload a tar archive to a container.
        API: PUT /containers/{id}/archive
        """
        query = urllib.parse.urlencode({"path": path})
        conn = UnixHttpConnection(self.socket_path)
        try:
            logger.debug("PUT /containers/%s/archive?%s", container_id, query)
            headers = {
                "Content-Type": "application/x-tar",
                "Content-Length": str(len(data)),
            }
            conn.request(
                "PUT",
                f"/containers/{container_id}/archive?{query}",
                body=data,
                headers=headers,
            )
            response = conn.getresponse()
            resp_data = response.read().decode("utf-8")

            if response.status < 400:
                return

            raise DockerException(f"Put Archive Error ({response.status}): {resp_data}")
        finally:
            conn.close()

    def get_archive(self, container_id: str, path: str) -> tuple[bytes, dict[str, Any]]:
        """
        Download a tar archive from a container.
        API: GET /containers/{id}/archive
        Returns: (raw_tar_bytes, stat_info)
        """
        query = urllib.parse.urlencode({"path": path})
        conn = UnixHttpConnection(self.socket_path)
        try:
            logger.debug("GET /containers/%s/archive?%s", container_id, query)
            conn.request("GET", f"/containers/{container_id}/archive?{query}")
            response = conn.getresponse()
            data = response.read()

            if response.status < 400:
                stat_header = response.getheader("X-Docker-Container-Path-Stat")
                stat_info = {}
                if stat_header:
                    try:
                        stat_info = json.loads(
                            base64.b64decode(stat_header).decode("utf-8")
                        )
                    except (json.JSONDecodeError, ValueError):
                        pass
                return data, stat_info

            raise DockerException(
                f"Get Archive Error ({response.status}): {data.decode('utf-8')}"
            )
        finally:
            conn.close()

    def copy_to_container(
        self, container_id: str, source_path: str, destination_path: str
    ) -> None:
        """
        Copy a local file or directory into a container.

        Args:
            container_id: ID of the container
            source_path: Local path to file or directory
            destination_path: Path in the container to copy to
        """
        source_path = os.path.abspath(source_path)
        if not os.path.exists(source_path):
            raise FileNotFoundError(f"Source path not found: {source_path}")

        tar_stream = io.BytesIO()
        with tarfile.open(fileobj=tar_stream, mode="w") as tar:
            # If it's a file, add it with just its basename so it extracts into destination_path
            # If it's a directory, add it recursively
            arcname = os.path.basename(source_path)
            tar.add(source_path, arcname=arcname)

        tar_stream.seek(0)
        self.put_archive(container_id, destination_path, tar_stream.getvalue())

    def copy_from_container(
        self, container_id: str, source_path: str, destination_path: str
    ) -> None:
        """
        Copy a file or directory from a container to the local filesystem.

        Args:
            container_id: ID of the container
            source_path: Path in the container to copy from
            destination_path: Local path to copy to (directory or file)
        """
        logger.info(
            "Copying %s from %s to %s...",
            source_path,
            container_id[:12],
            destination_path,
        )
        data, _ = self.get_archive(container_id, source_path)

        tar_stream = io.BytesIO(data)
        with tarfile.open(fileobj=tar_stream, mode="r") as tar:
            tar.extractall(path=destination_path)

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
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("auths", {})  # type: ignore
        except (OSError, json.JSONDecodeError):
            logger.warning("Failed to load Docker config from %s", config_path)
            return {}

    def _get_auth_for_image(self, image: str) -> dict[str, str] | None:
        """
        Resolve authentication for a given image from the local Docker config.
        """
        parts = image.split("/", 1)
        if "." in parts[0] or ":" in parts[0] or parts[0] == "localhost":
            registry = parts[0]
        else:
            registry = "index.docker.io/v1/"
            # Handle "docker.io/" prefix normalization if needed,
            # but usually config.json uses index.docker.io/v1/

        auths = self.load_docker_config()

        # Try exact match
        if registry in auths:
            return auths[registry]  # type: ignore

        # Try with https:// prefix
        if f"https://{registry}" in auths:
            return auths[f"https://{registry}"]  # type: ignore

        # Fallback for docker hub
        if registry == "docker.io" and "index.docker.io/v1/" in auths:
            return auths["index.docker.io/v1/"]  # type: ignore

        return None

    def push_image(
        self,
        image: str,
        tag: str | None = None,
        auth_config: dict[str, str] | None = None,
    ) -> Generator[dict[str, Any], None, None]:
        """
        Push an image.
        Equivalent to: docker push

        Yields:
            dict: Push progress objects
        """
        if tag:
            image = f"{image}:{tag}"

        logger.info("Pushing %s...", image)

        # Auto-detect auth if not provided
        if auth_config is None:
            auth_config = self._get_auth_for_image(image)
            if auth_config:
                logger.info("Using authenticated credentials for push.")

        headers = {}
        if auth_config:
            encoded_auth = base64.b64encode(json.dumps(auth_config).encode()).decode()
            headers["X-Registry-Auth"] = encoded_auth

        conn = UnixHttpConnection(self.socket_path)
        logger.debug("POST /images/%s/push", image)
        conn.request("POST", f"/images/{image}/push", headers=headers)
        response = conn.getresponse()

        yield from self._stream_json_response(response)
