import logging
from typing import TYPE_CHECKING, Any, List

from .docker_resource import DockerResource

if TYPE_CHECKING:
    from .client import DockerClient

logger = logging.getLogger(__name__)


class Network(DockerResource):
    """A Docker network."""

    @classmethod
    def list(cls, client: "DockerClient") -> List["Network"]:
        """
        List networks.
        Equivalent to: docker network ls
        """
        data = client._request("GET", "/networks")
        return [cls(client, network_data) for network_data in data]

    @classmethod
    def create(
        cls, client: "DockerClient", name: str, driver: str = "bridge", **kwargs: Any
    ) -> "Network":
        """
        Create a network.
        Equivalent to: docker network create
        """
        logger.info("Creating network %s (driver=%s)...", name, driver)
        payload = {"Name": name, "Driver": driver, **kwargs}
        res = client._request("POST", "/networks/create", body=payload)
        # res has only Id and Warning.
        return cls(client, {"Id": res.get("Id"), "Name": name, "Driver": driver})

    @property
    def name(self) -> str:
        return self.attrs.get("Name", "")

    @property
    def driver(self) -> str:
        return self.attrs.get("Driver", "")

    def remove(self) -> None:
        """
        Remove a network.
        Equivalent to: docker network rm
        """
        logger.info("Removing network %s...", self.resource_id)
        self.client._request("DELETE", f"/networks/{self.resource_id}")

    def inspect(self) -> dict[str, Any]:
        """
        Inspect a network.
        Equivalent to: docker network inspect
        """
        return self.client._request("GET", f"/networks/{self.resource_id}")  # type: ignore

    def __repr__(self) -> str:
        return f"<Network: {self.name}>"
