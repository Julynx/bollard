"""Container module. Provides the Container class for managing Docker containers."""

import base64
import json
import logging
import os
import shlex
import tarfile
import tempfile
import urllib.parse
from typing import IO, TYPE_CHECKING, Any, Generator, List

from .const import DEFAULT_KILL_SIGNAL, DEFAULT_TIMEOUT
from .docker_resource import DockerResource
from .exceptions import DockerException
from .image import Image

if TYPE_CHECKING:
    from .client import DockerClient

logger = logging.getLogger(__name__)


class Container(DockerResource):
    """A Docker container."""

    @classmethod
    def list(cls, client: "DockerClient", show_all: bool = False) -> List["Container"]:
        """List containers.

        Equivalent to: `docker ps`
        API: GET /containers/json

        Args:
            client: The DockerClient instance.
            show_all: If True, show all containers (including stopped ones).

        Returns:
            A list of Container objects.
        """
        params = {"all": "true"} if show_all else {}
        query = urllib.parse.urlencode(params)
        data = client._request("GET", f"/containers/json?{query}")
        return [cls(client, container_data) for container_data in data]

    @classmethod
    def run(
        cls,
        client: "DockerClient",
        image: str,
        command: str | List[str] | None = None,
        name: str | None = None,
        tty: bool = True,
        stdin_open: bool = True,
        environment: dict[str, str] | None = None,
        volumes: dict[str, str] | None = None,
        ports: dict[str, str | int] | None = None,
        runtime: str | None = None,
        gpu: bool = False,
        ipc_mode: str | None = None,
        **kwargs: Any,
    ) -> "Container":
        """Create and start a container.

        Defaults to -itd (Interactive, TTY, Detached) behavior.

        Args:
            client: The DockerClient instance.
            image: Image to run.
            command: Command to run.
            name: Name of the container.
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
        logger.info("Creating container for image '%s'...", image)

        payload = cls._build_container_config(
            image,
            command,
            stdin_open,
            tty,
            environment,
            volumes,
            ports,
            runtime,
            ipc_mode,
            gpu,
            **kwargs,
        )

        endpoint = "/containers/create"
        if name:
            endpoint += f"?name={name}"

        return cls._create_and_start(client, endpoint, payload, image, name)

    @classmethod
    def _build_container_config(
        cls,
        image: str,
        command: str | List[str] | None,
        stdin_open: bool,
        tty: bool,
        environment: dict[str, str] | None,
        volumes: dict[str, str] | None,
        ports: dict[str, str | int] | None,
        runtime: str | None,
        ipc_mode: str | None,
        gpu: bool,
        **kwargs: Any,
    ) -> dict[str, Any]:
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
            payload["Env"] = [f"{key}={value}" for key, value in environment.items()]

        host_config = cls._build_host_config(volumes, runtime, ipc_mode, gpu)

        if ports:
            exposed_ports, port_bindings = cls._configure_ports(ports)
            payload["ExposedPorts"] = exposed_ports
            host_config["PortBindings"] = port_bindings

        if host_config:
            payload["HostConfig"] = host_config

        if "HostConfig" in kwargs and host_config:
            payload["HostConfig"].update(kwargs.pop("HostConfig"))

        payload.update(kwargs)
        return payload

    @classmethod
    def _build_host_config(
        cls,
        volumes: dict[str, str] | None,
        runtime: str | None,
        ipc_mode: str | None,
        gpu: bool,
    ) -> dict[str, Any]:
        """Build the HostConfig dictionary."""
        host_config: dict[str, Any] = {}

        if volumes:
            host_config["Binds"] = [
                f"{host_path}:{container_path}"
                for host_path, container_path in volumes.items()
            ]

        if runtime:
            host_config["Runtime"] = runtime

        if ipc_mode:
            host_config["IpcMode"] = ipc_mode

        if gpu:
            host_config["DeviceRequests"] = [
                {
                    "Driver": "",
                    "Count": -1,
                    "DeviceIDs": [],
                    "Capabilities": [["gpu"]],
                    "Options": {},
                }
            ]

        return host_config

    @classmethod
    def _configure_ports(
        cls, ports: dict[str, str | int]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Format ports for ExposedPorts and PortBindings."""
        exposed_ports = {}
        port_bindings = {}

        for container_port, host_port in ports.items():
            formatted_port = container_port
            if "/" not in str(container_port):
                formatted_port = f"{container_port}/tcp"
            exposed_ports[formatted_port] = {}
            port_bindings[formatted_port] = [{"HostPort": str(host_port)}]
        return exposed_ports, port_bindings

    @classmethod
    def _create_and_start(
        cls,
        client: "DockerClient",
        endpoint: str,
        payload: dict[str, Any],
        image: str,
        name: str | None,
    ) -> "Container":
        try:
            create_res = client._request("POST", endpoint, body=payload)
        except DockerException as e:
            if "404" in str(e):
                logger.info("Image '%s' not found, pulling...", image)

                Image.pull(client, image)
                logger.info("Image pulled successfully. Retrying container creation...")
                create_res = client._request("POST", endpoint, body=payload)
            else:
                raise e

        container_id: str = create_res["Id"]

        try:
            logger.info("Starting container %s...", container_id[:12])
            client._request("POST", f"/containers/{container_id}/start")
        except Exception as e:
            logger.error(
                "Failed to start container %s: %s. Cleaning up...", container_id[:12], e
            )
            try:
                client._request("DELETE", f"/containers/{container_id}?force=true")
            except Exception:
                pass
            raise e

        container = cls(
            client,
            {
                "Id": container_id,
                "Image": image,
                "Names": [f"/{name}"] if name else [],
            },
        )
        try:
            container.reload()
        except Exception:
            pass
        return container

    def reload(self) -> None:
        """Refresh this object's data from the server."""
        self.attrs = self.client._request("GET", f"/containers/{self.resource_id}/json")

    @property
    def name(self) -> str:
        """The container name."""
        names = self.attrs.get("Names", [])
        if names:
            return names[0].lstrip("/")
        return ""

    @property
    def status(self) -> str:
        """The container status (e.g., 'running', 'exited')."""
        state = self.attrs.get("State")
        if isinstance(state, dict):
            return state.get("Status", "")
        if isinstance(state, str):
            return state
        return self.attrs.get("Status", "")

    @property
    def image(self) -> str:
        """The ID of the image used by this container."""
        return self.attrs.get("Image", "")

    def stop(self, timeout: int = DEFAULT_TIMEOUT) -> None:
        """Stop the container.

        Args:
            timeout: Seconds to wait for the container to stop before killing it.
        """
        logger.info("Stopping container %s...", self.resource_id[:12])
        self.client._request("POST", f"/containers/{self.resource_id}/stop?t={timeout}")

    def kill(self, signal: str = DEFAULT_KILL_SIGNAL) -> None:
        """Kill the container.

        Args:
            signal: Signal to send to the container (default: SIGKILL).
        """
        logger.info("Killing container %s...", self.resource_id[:12])
        self.client._request(
            "POST", f"/containers/{self.resource_id}/kill?signal={signal}"
        )

    def start(self) -> None:
        """Start the container."""
        logger.info("Starting container %s...", self.resource_id[:12])
        self.client._request("POST", f"/containers/{self.resource_id}/start")

    def restart(self, timeout: int = DEFAULT_TIMEOUT) -> None:
        """Restart the container.

        Args:
            timeout: Seconds to wait for the container to stop before restarting.
        """
        logger.info("Restarting container %s...", self.resource_id[:12])
        self.client._request(
            "POST", f"/containers/{self.resource_id}/restart?t={timeout}"
        )

    def remove(
        self,
        force: bool = False,
        remove_links: bool = False,
        remove_volumes: bool = False,
    ) -> None:
        """Remove the container.

        Args:
            force: If True, force removal of the container.
            remove_links: If True, remove the specified link and not the
            underlying container.
            remove_volumes: If True, remove the volumes associated with the container.
        """
        logger.info("Removing container %s...", self.resource_id[:12])
        params = {}
        if force:
            params["force"] = "true"
        if remove_links:
            params["link"] = "true"
        if remove_volumes:
            params["v"] = "true"

        query = urllib.parse.urlencode(params)
        self.client._request("DELETE", f"/containers/{self.resource_id}?{query}")

    def logs(
        self,
        tail: str | int = "all",
        stream: bool = False,
        follow: bool = False,
        encoding: str = "utf-8",
        errors: str = "ignore",
    ) -> str | Generator[str, None, None]:
        """Fetch container logs.

        Equivalent to: `docker logs`

        Args:
            tail: Number of lines to show from the end of the logs.
            stream: If True, return a generator yielding log lines.
            follow: If True, stream logs as they happen (implies stream=True).
            encoding: Encoding to use for decoding logs.
            errors: Error handling strategy for decoding logs.

        Returns:
            The container logs as a string (if stream=False) or
            a generator of strings (if stream=True).
        """
        params = {"stdout": "true", "stderr": "true", "tail": str(tail)}
        if follow:
            params["follow"] = "true"
            stream = True

        query = urllib.parse.urlencode(params)

        response = self.client._request(
            "GET", f"/containers/{self.resource_id}/logs?{query}", stream=True
        )

        if stream:
            return self._stream_log_response(response, encoding=encoding, errors=errors)

        try:
            data = response.read()
            return data.decode(encoding, errors=errors)
        finally:
            response.close()

    def _stream_log_response(
        self, response: Any, encoding: str = "utf-8", errors: str = "replace"
    ) -> Generator[str, None, None]:
        """
        Yields decoded lines from response.
        Note: This simplistic implementation doesn't strip Docker headers if Tty=False.
        """
        try:
            for line in response:
                yield line.decode(encoding, errors=errors)
        except Exception:
            pass
        finally:
            response.close()

    def exec(
        self,
        command: str | List[str],
        detach: bool = False,
        tty: bool = False,
        stream: bool = False,
        encoding: str = "utf-8",
        errors: str = "ignore",
    ) -> str | Generator[str, None, None]:
        """Execute a command in a running container.

        Equivalent to: `docker exec`

        Args:
            command: Command to execute.
            detach: If True, run command in background.
            tty: If True, allocate a pseudo-TTY.
            stream: If True, stream the output.
            encoding: Encoding to use for decoding output.
            errors: Error handling strategy for decoding output.

        Returns:
            The output of the command (if not detached) or the exec ID.
        """
        payload: dict[str, Any] = {
            "AttachStdin": False,
            "AttachStdout": True,
            "AttachStderr": True,
            "Tty": tty,
            "Cmd": command if isinstance(command, list) else shlex.split(command),
        }
        res: dict[str, Any] = self.client._request(
            "POST", f"/containers/{self.resource_id}/exec", body=payload
        )
        exec_id: str = res["Id"]

        start_payload = {"Detach": detach, "Tty": tty}

        if detach:
            self.client._request("POST", f"/exec/{exec_id}/start", body=start_payload)
            return exec_id

        # Streaming request
        response = self.client._request(
            "POST", f"/exec/{exec_id}/start", body=start_payload, stream=True
        )

        if stream:
            if tty:
                return self._stream_log_response(
                    response, encoding=encoding, errors=errors
                )

            return self._stream_multiplexed(response, encoding=encoding, errors=errors)

        try:
            if tty:
                return response.read().decode(encoding, errors=errors)

            return self._read_multiplexed_response(
                response, encoding=encoding, errors=errors
            )
        finally:
            response.close()

    def _stream_multiplexed(
        self, response: Any, encoding: str = "utf-8", errors: str = "replace"
    ) -> Generator[str, None, None]:
        """Generator for multiplexed streams."""
        try:
            while True:
                header_length = 8
                header_size_idx = 4
                header = response.read(header_length)
                if not header or len(header) < header_length:
                    break
                payload_size = int.from_bytes(
                    header[header_size_idx:header_length],
                    "big",
                )
                payload = response.read(payload_size)
                if not payload:
                    break
                yield payload.decode(encoding, errors=errors)
        finally:
            response.close()

    def _read_multiplexed_response(
        self, response: Any, encoding: str = "utf-8", errors: str = "ignore"
    ) -> str:
        """
        Reads a Docker multiplexed response (when Tty=False) and combines stdout/stderr.
        """
        output = []
        while True:
            header_length = 8
            header_size_idx = 4
            header = response.read(header_length)
            if not header or len(header) < header_length:
                break

            stream_type = header[0]
            payload_size = int.from_bytes(
                header[header_size_idx:header_length],
                "big",
            )

            payload = response.read(payload_size)
            stdout = 1
            stderr = 2
            if stream_type in (stdout, stderr):
                output.append(payload.decode(encoding, errors=errors))

        return "".join(output)

    def put_archive(self, path: str, data: Any) -> None:
        """Upload a tar archive to a container.

        Args:
            path: Path in the container to extract the archive to.
            data: The tar archive data as bytes.
        """
        query = urllib.parse.urlencode({"path": path})

        headers = {
            "Content-Type": "application/x-tar",
        }

        self.client._request(
            "PUT",
            f"/containers/{self.resource_id}/archive?{query}",
            body=data,
            headers=headers,
        )

    def get_archive(self, path: str) -> tuple[IO[bytes], dict[str, Any]]:
        """Download a tar archive from a container.

        Args:
            path: Path in the container to download.

        Returns:
            A tuple containing the archive data (stream) and a
            dictionary of file statistics.
        """
        query = urllib.parse.urlencode({"path": path})
        logger.debug("GET /containers/%s/archive?%s", self.resource_id, query)

        response = self.client._request(
            "GET", f"/containers/{self.resource_id}/archive?{query}", stream=True
        )

        stat_header = response.getheader("X-Docker-Container-Path-Stat")
        stat_info = {}
        if stat_header:
            try:
                stat_info = json.loads(base64.b64decode(stat_header).decode("utf-8"))
            except (json.JSONDecodeError, ValueError):
                pass

        return response, stat_info

    def copy_to(self, source_path: str, destination_path: str) -> None:
        """Copy a local file or directory into a container.

        Args:
            source_path: Local path to the file or directory.
            destination_path: Path in the container.

        Raises:
            FileNotFoundError: If the source path does not exist.
        """

        source_path = os.path.abspath(source_path)
        if not os.path.exists(source_path):
            raise FileNotFoundError(f"Source path not found: {source_path}")

        temp_tar = tempfile.TemporaryFile()
        try:
            with tarfile.open(fileobj=temp_tar, mode="w") as tar:
                arcname = os.path.basename(source_path)
                tar.add(source_path, arcname=arcname)

            temp_tar.seek(0)
            self.put_archive(destination_path, temp_tar)
        finally:
            temp_tar.close()

    def copy_from(self, source_path: str, destination_path: str) -> None:
        """Copy a file or directory from a container to the local filesystem.

        Args:
            source_path: Path in the container to copy from.
            destination_path: Local path to copy to.
        """

        logger.info(
            "Copying %s from %s to %s...",
            source_path,
            self.resource_id[:12],
            destination_path,
        )
        response, _ = self.get_archive(source_path)

        try:
            with tarfile.open(fileobj=response, mode="r|") as tar:
                tar.extractall(path=destination_path)
        finally:
            response.close()

    def __repr__(self) -> str:
        return f"<Container: {self.resource_id[:12]}>"
