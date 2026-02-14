"""Docker resource module. Provides the DockerResource class for managing
Docker resources."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .client import DockerClient


class DockerResource:
    """Base class for Docker resources."""

    def __init__(
        self, client: "DockerClient", attrs: dict[str, Any] | None = None
    ) -> None:
        """Constructor for DockerResource."""
        self.client = client
        self.attrs = attrs or {}

    @property
    def resource_id(self) -> str:
        """Return the resource ID."""
        return self.attrs.get("Id", "")

    def reload(self) -> None:
        """Refresh this object's data from the server."""
        raise NotImplementedError
