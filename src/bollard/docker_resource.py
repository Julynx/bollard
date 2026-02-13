from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .client import DockerClient


class DockerResource:
    """Base class for Docker resources."""

    def __init__(
        self, client: "DockerClient", attrs: dict[str, Any] | None = None
    ) -> None:
        self.client = client
        self.attrs = attrs or {}

    @property
    def resource_id(self) -> str:
        return self.attrs.get("Id", "")

    def reload(self) -> None:
        """Refresh this object's data from the server."""
        # This base method might not know how to fetch,
        # but subclasses like Container do.
        raise NotImplementedError
