"""Network module. Provides the Network class for managing Docker networks."""

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
        """List networks.

        Equivalent to: `docker network ls`

        Args:
            client: The DockerClient instance.

        Returns:
            A list of Network objects.
        """
        data = client._request("GET", "/networks")
        return [cls(client, network_data) for network_data in data]

    @classmethod
    def create(
        cls, client: "DockerClient", name: str, driver: str = "bridge", **kwargs: Any
    ) -> "Network":
        """Create a network.

        Equivalent to: `docker network create`

        Args:
            client: The DockerClient instance.
            name: Name of the network.
            driver: Network driver to use.
            **kwargs: Additional arguments passed to network creation.

        Returns:
            The created Network object.
        """
        logger.info("Creating network %s (driver=%s)...", name, driver)
        payload = {"Name": name, "Driver": driver, **kwargs}
        res = client._request("POST", "/networks/create", body=payload)
        return cls(client, {"Id": res.get("Id"), "Name": name, "Driver": driver})

    @property
    def name(self) -> str:
        """The network name."""
        return self.attrs.get("Name", "")

    @property
    def driver(self) -> str:
        """The network driver."""
        return self.attrs.get("Driver", "")

    def remove(self) -> None:
        """Remove the network.

        Equivalent to: `docker network rm`
        """
        logger.info("Removing network %s...", self.resource_id)
        self.client._request("DELETE", f"/networks/{self.resource_id}")

    def inspect(self) -> dict[str, Any]:
        """Inspect the network.

        Equivalent to: `docker network inspect`

        Returns:
            A dictionary containing network attributes.
        """
        return self.client._request("GET", f"/networks/{self.resource_id}")  # type: ignore

    def __repr__(self) -> str:
        return f"<Network: {self.name}>"
