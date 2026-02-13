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
        """
        List volumes.
        Equivalent to: docker volume ls
        """
        data = client._request("GET", "/volumes")
        volumes = data.get("Volumes") or []
        return [cls(client, volume_data) for volume_data in volumes]

    @classmethod
    def create(
        cls, client: "DockerClient", name: str, driver: str = "local", **kwargs: Any
    ) -> "Volume":
        """
        Create a volume.
        Equivalent to: docker volume create
        """
        logger.info("Creating volume %s (driver=%s)...", name, driver)
        payload = {"Name": name, "Driver": driver, **kwargs}
        res = client._request("POST", "/volumes/create", body=payload)
        return cls(client, res)

    @property
    def name(self) -> str:
        return self.attrs.get("Name", "")

    @property
    def driver(self) -> str:
        return self.attrs.get("Driver", "")

    def remove(self, force: bool = False) -> None:
        """
        Remove a volume.
        Equivalent to: docker volume rm
        """
        logger.info("Removing volume %s...", self.name)
        # Use name, not ID for volume
        query = urllib.parse.urlencode({"force": "true"} if force else {})
        self.client._request("DELETE", f"/volumes/{self.name}?{query}")

    def inspect(self) -> dict[str, Any]:
        """
        Inspect a volume.
        Equivalent to: docker volume inspect
        """
        return self.client._request("GET", f"/volumes/{self.name}")  # type: ignore

    def __repr__(self) -> str:
        return f"<Volume: {self.name}>"
