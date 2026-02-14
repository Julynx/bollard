"""API for Docker Engine.

This package provides a Pythonic client for interacting
with the Docker (or Podman) Engine API.
"""

from .client import DockerClient
from .exceptions import DockerException
from .models import Container, Image, Network, Volume

__all__ = ["DockerClient", "DockerException", "Container", "Image", "Network", "Volume"]

import logging
import sys

# Configure logging for the entire package
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Only add handler if none exists to avoid duplicates
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
