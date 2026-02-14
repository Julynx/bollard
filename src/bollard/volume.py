"""Volume module. Provides the Volume class for managing Docker volumes."""

import logging
import urllib.parse
from typing import TYPE_CHECKING, Any, List

from .docker_resource import DockerResource

if TYPE_CHECKING:
    from .client import DockerClient

logger = logging.getLogger(__name__)


class Volume(DockerResource):
    """A Docker volume."""

    @classmethod
    def list(cls, client: "DockerClient") -> List["Volume"]:
        """List volumes.

        Equivalent to: `docker volume ls`

        Args:
            client: The DockerClient instance.

        Returns:
            A list of Volume objects.
        """
        data = client._request("GET", "/volumes")
        volumes = data.get("Volumes") or []
        return [cls(client, volume_data) for volume_data in volumes]

    @classmethod
    def create(
        cls, client: "DockerClient", name: str, driver: str = "local", **kwargs: Any
    ) -> "Volume":
        """Create a volume.

        Equivalent to: `docker volume create`

        Args:
            client: The DockerClient instance.
            name: Name of the volume.
            driver: Volume driver to use.
            **kwargs: Additional arguments passed to volume creation.

        Returns:
            The created Volume object.
        """
        logger.info("Creating volume %s (driver=%s)...", name, driver)
        payload = {"Name": name, "Driver": driver, **kwargs}
        res = client._request("POST", "/volumes/create", body=payload)
        return cls(client, res)

    @property
    def name(self) -> str:
        """The volume name."""
        return self.attrs.get("Name", "")

    @property
    def driver(self) -> str:
        """The volume driver."""
        return self.attrs.get("Driver", "")

    def remove(self, force: bool = False) -> None:
        """Remove the volume.

        Equivalent to: `docker volume rm`

        Args:
            force: If True, force removal of the volume.
        """
        logger.info("Removing volume %s...", self.name)
        # Use name, not ID for volume
        query = urllib.parse.urlencode({"force": "true"} if force else {})
        self.client._request("DELETE", f"/volumes/{self.name}?{query}")

    def inspect(self) -> dict[str, Any]:
        """Inspect the volume.

        Equivalent to: `docker volume inspect`

        Returns:
            A dictionary containing volume attributes.
        """
        return self.client._request("GET", f"/volumes/{self.name}")  # type: ignore

    def __repr__(self) -> str:
        return f"<Volume: {self.name}>"
