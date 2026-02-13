import base64
import http.client
import io
import json
import logging
import shlex
import tarfile
import urllib.parse
from typing import TYPE_CHECKING, Any

from .const import DEFAULT_KILL_SIGNAL, DEFAULT_TIMEOUT
from .docker_resource import DockerResource
from .exceptions import DockerException
from .transport import UnixHttpConnection

if TYPE_CHECKING:
    from .client import DockerClient

logger = logging.getLogger(__name__)


class Container(DockerResource):
    """A Docker container."""

    @classmethod
    def list(cls, client: "DockerClient", show_all: bool = False) -> list["Container"]:
        """
        List containers.
        Equivalent to: docker ps
        API: GET /containers/json
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
        command: str | list[str] | None = None,
        name: str | None = None,
        # Default behavior: Keep stdin open and allocate a pseudo-TTY.
        # This prevents shell containers (alpine, ubuntu) from exiting immediately.
        detach: bool = True,
        tty: bool = True,
        stdin_open: bool = True,
        environment: dict[str, str] | None = None,
        volumes: dict[str, str] | None = None,  # {host_path: container_path}
        ports: dict[str, str | int] | None = None,  # {container_port: host_port}
        runtime: str | None = None,
        gpu: bool = False,
        ipc_mode: str | None = None,
        **kwargs: Any,
    ) -> "Container":
        """
        Create and start a container.
        Defaults to -itd (Interactive, TTY, Detached) behavior.
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
            payload["Env"] = [f"{key}={value}" for key, value in environment.items()]

        host_config: dict[str, Any] = {}

        if volumes:
            host_config["Binds"] = [
                f"{host_path}:{container_path}"
                for host_path, container_path in volumes.items()
            ]

        if ports:
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

        if "HostConfig" in kwargs and host_config:
            payload["HostConfig"].update(kwargs.pop("HostConfig"))

        payload.update(kwargs)

        endpoint = "/containers/create"
        if name:
            endpoint += f"?name={name}"

        try:
            create_res = client._request("POST", endpoint, body=payload)
        except DockerException as e:
            if "404" in str(e):
                logger.info("Image '%s' not found, pulling...", image)
                # We need to pull the image.
                # Assuming client has pull_image or we use Image.pull
                # client methods might be refactored, so safer to use Image.pull
                from .image import Image

                for _ in Image.pull(client, image):
                    pass
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
                # We can call remove logic here
                # Container(client, {"Id": container_id}).remove(force=True)
                # But to avoid recursion or instantiation, use request directly:
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
        names = self.attrs.get("Names", [])
        if names:
            return names[0].lstrip("/")
        return ""

    @property
    def status(self) -> str:
        state = self.attrs.get("State")
        if isinstance(state, dict):
            return state.get("Status", "")
        if isinstance(state, str):
            return state
        return self.attrs.get("Status", "")

    @property
    def image(self) -> str:
        return self.attrs.get("Image", "")

    def stop(self, timeout: int = DEFAULT_TIMEOUT) -> None:
        """
        Stop the container.

        Args:
            timeout: Seconds to wait for the container to stop before killing it.
        """
        logger.info("Stopping container %s...", self.resource_id[:12])
        self.client._request("POST", f"/containers/{self.resource_id}/stop?t={timeout}")

    def kill(self, signal: str = DEFAULT_KILL_SIGNAL) -> None:
        """
        Kill the container.

        Args:
            signal: Signal to send to the container (default: SIGKILL).
        """
        logger.info("Killing container %s...", self.resource_id[:12])
        self.client._request(
            "POST", f"/containers/{self.resource_id}/kill?signal={signal}"
        )

    def start(self) -> None:
        logger.info("Starting container %s...", self.resource_id[:12])
        self.client._request("POST", f"/containers/{self.resource_id}/start")

    def restart(self, timeout: int = DEFAULT_TIMEOUT) -> None:
        """
        Restart the container.

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

    def logs(self, tail: str | int = "all") -> str:
        """
        Fetch container logs.
        Equivalent to: docker logs
        """
        params = {"stdout": "true", "stderr": "true", "tail": str(tail)}
        query = urllib.parse.urlencode(params)
        conn = UnixHttpConnection(self.client.socket_path)
        try:
            logger.debug("GET /containers/%s/logs?%s", self.resource_id, query)
            conn.request("GET", f"/containers/{self.resource_id}/logs?{query}")
            response = conn.getresponse()
            data = response.read()
            return data.decode("utf-8", errors="ignore")
        finally:
            conn.close()

    def exec(
        self, command: str | list[str], detach: bool = False, tty: bool = False
    ) -> str:
        """
        Execute a command in a running container.
        Equivalent to: docker exec
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

        conn = UnixHttpConnection(self.client.socket_path)
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
                return response.read().decode("utf-8", errors="ignore")
            else:
                return self._read_multiplexed_response(response)
        finally:
            conn.close()

    def _read_multiplexed_response(self, response: http.client.HTTPResponse) -> str:
        """
        Reads a Docker multiplexed response (when Tty=False) and combines stdout/stderr.
        """
        output = []
        while True:
            header = response.read(8)
            if not header:
                break
            if len(header) < 8:
                break

            stream_type = header[0]
            payload_size = int.from_bytes(header[4:8], "big")

            payload = response.read(payload_size)
            if stream_type in (1, 2):  # stdout or stderr
                output.append(payload.decode("utf-8", errors="ignore"))

        return "".join(output)

    def put_archive(self, path: str, data: bytes) -> None:
        """
        Upload a tar archive to a container.
        """
        query = urllib.parse.urlencode({"path": path})
        conn = UnixHttpConnection(self.client.socket_path)
        try:
            logger.debug("PUT /containers/%s/archive?%s", self.resource_id, query)
            headers = {
                "Content-Type": "application/x-tar",
                "Content-Length": str(len(data)),
            }
            conn.request(
                "PUT",
                f"/containers/{self.resource_id}/archive?{query}",
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

    def get_archive(self, path: str) -> tuple[bytes, dict[str, Any]]:
        """
        Download a tar archive from a container.
        """
        query = urllib.parse.urlencode({"path": path})
        conn = UnixHttpConnection(self.client.socket_path)
        try:
            logger.debug("GET /containers/%s/archive?%s", self.resource_id, query)
            conn.request("GET", f"/containers/{self.resource_id}/archive?{query}")
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

    def copy_to(self, source_path: str, destination_path: str) -> None:
        """
        Copy a local file or directory into a container.
        """
        import os  # Import here or at top level

        source_path = os.path.abspath(source_path)
        if not os.path.exists(source_path):
            raise FileNotFoundError(f"Source path not found: {source_path}")

        tar_stream = io.BytesIO()
        with tarfile.open(fileobj=tar_stream, mode="w") as tar:
            arcname = os.path.basename(source_path)
            tar.add(source_path, arcname=arcname)

        tar_stream.seek(0)
        self.put_archive(destination_path, tar_stream.getvalue())

    def copy_from(self, source_path: str, destination_path: str) -> None:
        """
        Copy a file or directory from a container to the local filesystem.
        """

        logger.info(
            "Copying %s from %s to %s...",
            source_path,
            self.resource_id[:12],
            destination_path,
        )
        data, _ = self.get_archive(source_path)

        tar_stream = io.BytesIO(data)
        with tarfile.open(fileobj=tar_stream, mode="r") as tar:
            tar.extractall(path=destination_path)

    def __repr__(self) -> str:
        return f"<Container: {self.resource_id[:12]}>"
