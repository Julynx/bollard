"""
Client module. Provides functionality for the Docker/Podman Engine API.
"""

import contextlib
import http.client
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Generator, Literal, overload

from .const import DEFAULT_TIMEOUT, PIPE_WAIT_TIMEOUT_S, WINDOWS_PIPE_CANDIDATES
from .exceptions import DockerException
from .models import Container, Image, Network, Volume
from .transport import UnixHttpConnection

# Configure logging
logger = logging.getLogger(__name__)
wire_logger = logging.getLogger("bollard.wire")


class DockerClient:
    """A Pythonic client for the Docker/Podman Engine API.

    Supports:
        - Context Manager usage (`with DockerClient() as client:`)
        - Auto-starting (and initializing) the Podman machine when its
          API endpoint is missing
        - Ephemeral container contexts
    """

    def __init__(self, socket_path: str | None = None) -> None:
        if socket_path is None:
            if sys.platform == "win32":
                self.socket_path = self._discover_windows_pipe()
            elif sys.platform == "darwin":
                self.socket_path = self._discover_macos_socket()
            else:
                self.socket_path = self._discover_linux_socket()
        else:
            self.socket_path = socket_path

        self._conn: UnixHttpConnection | None = None

    def _discover_windows_pipe(self) -> str:
        """Attempts to find a valid named pipe for Docker or Podman.

        Checks env vars DOCKER_HOST and DOCKER_SOCK first. If none are
        found, probes the known engine pipes, brings the Podman machine
        up when needed (initializing it first if it does not exist yet),
        and probes again until the wait deadline.

        Returns:
            The path to the named pipe.

        Raises:
            FileNotFoundError: If no engine endpoint became reachable.
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

        # 2. Try to find an existing pipe
        found = self._wait_for_pipe(0.0)
        if found:
            logger.info("Connected to Docker/Podman via %s", found)
            return found

        # 3. Bring the Podman machine up, then wait for its endpoint
        logger.info(
            "No active Docker/Podman pipe found. Starting the Podman machine..."
        )
        failure = self._bring_up_podman_machine()
        found = self._wait_for_pipe(PIPE_WAIT_TIMEOUT_S)
        if found:
            logger.info("Connected to Docker/Podman via %s", found)
            return found

        raise FileNotFoundError(
            "No Docker or Podman API endpoint is reachable. The Podman"
            " machine could not be started."
            + (f" The last attempt said: {failure}" if failure else "")
            + " Check that virtualization is enabled and try running"
            " `podman machine start` in a terminal for details."
        )

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

        # Try activating the Podman user socket via systemd first
        subprocess.run(
            ["systemctl", "--user", "enable", "--now", "podman.socket"],
            check=False,
            capture_output=True,
        )

        if podman_socket.exists():
            return str(podman_socket)

        docker_socket = Path("/run/docker.sock")
        if docker_socket.exists():
            return str(docker_socket)

        raise FileNotFoundError("Neither Podman nor Docker socket could be found.")

    def _discover_macos_socket(self) -> str:
        """Returns the location of the socket on macOS.

        Checks DOCKER_HOST, common Docker Desktop locations, and the
        socket of the default Podman machine, starting the machine when
        it is not running yet.

        Returns:
            The path to the socket.

        Raises:
            FileNotFoundError: If no engine endpoint became reachable.
        """
        env_host = os.environ.get("DOCKER_HOST")
        if env_host:
            return env_host

        docker_desktop = Path.home() / ".docker" / "run" / "docker.sock"
        if docker_desktop.exists():
            return str(docker_desktop)

        socket_path = self._podman_machine_socket()
        if socket_path:
            return socket_path

        logger.info(
            "No active Docker/Podman socket found. Starting the Podman machine..."
        )
        self._bring_up_podman_machine()
        socket_path = self._podman_machine_socket()
        if socket_path:
            return socket_path

        raise FileNotFoundError(
            "No Docker or Podman API endpoint is reachable. The Podman"
            " machine could not be started. Try running `podman machine"
            " start` in a terminal for details."
        )

    def _run_process(
        self, command: list[str], timeout: float | None = None
    ) -> tuple[bool, str]:
        """Run one command and return (ok, combined output)."""
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8",
                errors="replace",
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, str(exc)
        output = (completed.stdout or "") + (completed.stderr or "")
        return completed.returncode == 0, output.strip()

    def _run_podman(
        self, args: list[str], timeout: float | None = None
    ) -> tuple[bool, str]:
        """Run one podman command and return (ok, combined output).

        No timeout is enforced by default: machine start can take as
        long as it takes on slow machines. Callers may pass one.
        """
        return self._run_process(["podman", *args], timeout=timeout)

    def _wait_for_pipe(self, timeout: float) -> str | None:
        """Poll the known engine pipes, returning the first reachable one."""
        deadline = time.monotonic() + timeout
        while True:
            for pipe in WINDOWS_PIPE_CANDIDATES:
                if self._check_pipe(pipe):
                    return pipe
            if time.monotonic() >= deadline:
                return None
            time.sleep(1.0)

    def _bring_up_podman_machine(self) -> str | None:
        """Start the Podman machine, initializing or restarting as needed.

        A failed start means either a missing machine (initialize one
        and start it) or a registered machine whose endpoint never came
        up (restart it, since `start` refuses to run twice).

        Returns:
            None on success, or a human-readable failure description
            combining the outputs of the commands that failed.
        """
        ssh_note = self._ensure_ssh_keygen()
        if ssh_note:
            return ssh_note
        ok, output = self._run_podman(["machine", "start"])
        if ok:
            return None
        # The machine may already be running with its endpoint still
        # missing; give it a short grace period before reconfiguring.
        if self._wait_for_pipe(10.0):
            return None
        if self._machine_exists():
            logger.info(
                "The Podman machine exists but its endpoint is down."
                " Restarting it..."
            )
            self._run_podman(["machine", "stop"])
            ok, output = self._run_podman(["machine", "start"])
            if ok or self._wait_for_pipe(10.0):
                return None
            return "`podman machine start` failed: " + output[-400:]
        ok, init_output = self._run_podman(["machine", "init"])
        if not ok:
            return (
                "`podman machine start` failed: "
                + output[-400:]
                + " | `podman machine init` failed: "
                + init_output[-400:]
            )
        ok, output = self._run_podman(["machine", "start"])
        if not ok:
            return "`podman machine start` failed: " + output[-400:]
        return None

    def _machine_exists(self) -> bool:
        """Whether at least one Podman machine is registered."""
        ok, output = self._run_podman(["machine", "inspect"])
        if not ok:
            return False
        try:
            return len(json.loads(output)) > 0
        except (json.JSONDecodeError, TypeError):
            return False

    def _ensure_ssh_keygen(self) -> str | None:
        """Make ssh-keygen reachable, as `podman machine init` needs it.

        On Windows the OpenSSH client is an optional feature: when it is
        enabled its directory is usually on PATH, but it may also exist
        off-PATH, or be disabled entirely. Probe the known locations,
        and as a last resort enable the built-in feature (through the
        UAC prompt when required).

        Returns:
            None when ssh-keygen is available, else a failure note.
        """
        if shutil.which("ssh-keygen"):
            return None
        if sys.platform != "win32":
            return None
        for directory in self._windows_openssh_dirs():
            if Path(directory, "ssh-keygen.exe").exists():
                self._prepend_path(directory)
                break
        if shutil.which("ssh-keygen"):
            logger.info("Using ssh-keygen from an off-PATH OpenSSH directory.")
            return None
        logger.info("ssh-keygen not found; enabling the Windows OpenSSH client...")
        self._enable_windows_openssh()
        for directory in self._windows_openssh_dirs():
            if Path(directory, "ssh-keygen.exe").exists():
                self._prepend_path(directory)
                break
        if shutil.which("ssh-keygen"):
            return None
        return (
            "podman needs the Windows OpenSSH client (ssh-keygen) to create"
            " its machine, and enabling it did not work. Install the"
            " 'OpenSSH Client' optional feature in Windows Settings"
            " (Apps > Optional features), then try again."
        )

    def _windows_openssh_dirs(self) -> list[str]:
        """Directories where Windows and Git ship ssh-keygen.exe."""
        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        program_files_x86 = os.environ.get(
            "ProgramFiles(x86)", r"C:\Program Files (x86)"
        )
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        directories = [
            os.path.join(system_root, "System32", "OpenSSH"),
            os.path.join(program_files, "OpenSSH"),
            os.path.join(program_files_x86, "OpenSSH"),
        ]
        if local_app_data:
            directories.append(
                os.path.join(local_app_data, "Programs", "Git", "usr", "bin")
            )
        directories.append(os.path.join(program_files, "Git", "usr", "bin"))
        return directories

    def _prepend_path(self, directory: str) -> None:
        """Make a directory visible to the podman subprocesses."""
        current = os.environ.get("PATH", "")
        if directory.casefold() not in {
            entry.casefold() for entry in current.split(os.pathsep)
        }:
            os.environ["PATH"] = directory + os.pathsep + current

    def _enable_windows_openssh(self) -> None:
        """Enable the built-in OpenSSH client, best-effort.

        The plain call needs an elevated shell; when it is rejected, the
        same command is relaunched through the UAC prompt so the user
        only has to click 'Yes'.
        """
        command = "Add-WindowsCapability -Online -Name OpenSSH.Client~~~~0.0.1.0"
        self._run_process(["powershell", "-NoProfile", "-Command", command])
        if shutil.which("ssh-keygen"):
            return
        elevated = (
            "Start-Process powershell -Verb RunAs -Wait -ArgumentList"
            " '-NoProfile','-Command','" + command + "'"
        )
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", elevated],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except (OSError, subprocess.TimeoutExpired):
            pass

    def _podman_machine_socket(self) -> str | None:
        """Return the default machine's Unix socket path, if it exists."""
        ok, output = self._run_podman(["machine", "inspect"])
        if not ok:
            return None
        try:
            machines = json.loads(output)
            for machine in machines:
                connection = machine.get("ConnectionInfo") or {}
                for key in ("DockerSocket", "PodmanSocket"):
                    raw = (connection.get(key) or {}).get("Path")
                    if raw and Path(str(raw)).exists():
                        return str(raw)
        except (json.JSONDecodeError, TypeError, AttributeError):
            return None
        return None

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

        if body:
            if isinstance(body, dict):
                json_body = json.dumps(body)
                headers["Content-Type"] = "application/json"
                headers["Content-Length"] = str(len(json_body))
                return json_body

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

        http_error = 400
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
            if response.status >= http_error:
                data = response.read().decode("utf-8")
                raise DockerException(f"Docker API Error ({response.status}): {data}")
            return response

        try:
            wire_logger.debug(
                "Request: %s %s\nHeaders: %s",
                method,
                endpoint,
                headers,
            )

            data = response.read().decode("utf-8")

            if response.status < http_error:
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
        http_error = 400
        try:
            if response.status >= http_error:
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
