"""
Constants for the Bollard library.
"""

# Default timeout for Docker operations (in seconds)
DEFAULT_TIMEOUT = 10

# Timeout for one podman machine command (start, init, inspect)
PODMAN_COMMAND_TIMEOUT_S = 300.0

# How long to wait for an engine endpoint to appear after a machine start
PIPE_WAIT_TIMEOUT_S = 60.0

# Windows named pipes a container engine may expose its API on
WINDOWS_PIPE_CANDIDATES = (
    "\\\\.\\pipe\\docker_engine",
    "\\\\.\\pipe\\podman-machine-default",
    "\\\\.\\pipe\\podman-machine",
    "\\\\.\\pipe\\docker_cli",
)

# Default signal for killing a container
DEFAULT_KILL_SIGNAL = "SIGKILL"

# Default driver for network creation
DEFAULT_NETWORK_DRIVER = "bridge"

# Default driver for volume creation
DEFAULT_VOLUME_DRIVER = "local"
