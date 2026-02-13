import http.client
import os
import socket
import sys
import time
from typing import IO, Any

from .const import DEFAULT_TIMEOUT


class NpipeSocket:
    """A wrapper for Windows Named Pipes to mimic a socket."""

    def __init__(self) -> None:
        self._handle: IO[bytes] | None = None

    def connect(self, address: str, timeout: float = DEFAULT_TIMEOUT) -> None:
        """Connect to the named pipe at the given address."""
        start_time = time.time()
        # Local import to avoid circular dependency
        import logging

        logger = logging.getLogger("bollard.transport")

        logger.debug("Attempting to connect to named pipe: %s", address)
        while True:
            try:
                file_descriptor = os.open(address, os.O_RDWR | os.O_BINARY)
                self._handle = os.fdopen(file_descriptor, "r+b", buffering=0)
                logger.debug("Successfully connected to named pipe: %s", address)
                break

            except FileNotFoundError:
                raise
            except OSError:
                if time.time() - start_time > timeout:
                    raise TimeoutError(
                        f"Timed out connecting to {address} after {timeout}s"
                    )
                time.sleep(0.1)

    def sendall(self, data: bytes) -> None:
        """Send data to the pipe."""
        if self._handle is None:
            raise OSError("Socket not connected")
        self._handle.write(data)

    def recv(self, buffer_size: int) -> bytes:
        """Receive data from the pipe."""
        if self._handle is None:
            raise OSError("Socket not connected")
        return self._handle.read(buffer_size) or b""

    def makefile(
        self, mode: str, buffer_size: int | None = None, **kwargs: Any
    ) -> IO[bytes]:  # pylint: disable=unused-argument
        """Create a file object from the pipe handle."""
        if mode != "rb":
            raise NotImplementedError(f"makefile mode {mode} not supported")
        if self._handle is None:
            raise OSError("Socket not connected")
        return os.fdopen(os.dup(self._handle.fileno()), mode, buffering=-1)

    def close(self) -> None:
        """Close the socket handle."""
        if self._handle:
            self._handle.close()
            self._handle = None


class UnixHttpConnection(http.client.HTTPConnection):
    """
    Custom HTTP Connection that connects to a Unix Socket
    instead of a TCP host:port.
    On Windows this uses Named Pipes.
    """

    def __init__(self, socket_path: str) -> None:
        super().__init__("localhost")
        self.socket_path = socket_path

    def connect(self) -> None:
        import logging

        logger = logging.getLogger("bollard.transport")

        logger.debug("Connecting to socket path: %s", self.socket_path)
        if hasattr(socket, "AF_UNIX"):
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.sock.connect(self.socket_path)
        elif sys.platform == "win32":
            # Windows Named Pipe Support
            self.sock = NpipeSocket()
            self.sock.connect(self.socket_path)
        else:
            raise NotImplementedError("Unix sockets not supported on this platform")
