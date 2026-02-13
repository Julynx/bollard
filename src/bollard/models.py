from typing import TYPE_CHECKING, Any, Generator

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
    def id(self) -> str:
        return self.attrs.get("Id", "")

    def reload(self) -> None:
        """Refresh this object's data from the server."""
        # This base method might not know how to fetch,
        # but subclasses like Container do.
        raise NotImplementedError


class Container(DockerResource):
    """A Docker container."""

    def reload(self) -> None:
        """Refresh this object's data from the server."""
        self.attrs = self.client.inspect_container(self.id)
        if "Id" not in self.attrs:
            # Some implementations put it under "Config" or "State"? NO, top level.
            # But just in case
            pass

    @property
    def name(self) -> str:
        # Names are usually "/name", so strip leading slash
        names = self.attrs.get("Names", [])
        if names:
            return names[0].lstrip("/")
        return ""

    @property
    def status(self) -> str:
        state = self.attrs.get("State")
        if isinstance(state, dict):
            return state.get("Status", "")
        if isinstance(state, str):
            return state
        return self.attrs.get("Status", "")

    @property
    def image(self) -> str:
        return self.attrs.get("Image", "")

    def stop(self, timeout: int = 10) -> None:
        self.client.stop_container(self.id, timeout)

    def kill(self, signal: str = "SIGKILL") -> None:
        self.client.kill_container(self.id, signal)

    def start(self) -> None:
        self.client.start_container(self.id)

    def restart(self, timeout: int = 10) -> None:
        self.client.restart_container(self.id, timeout)

    def remove(
        self,
        force: bool = False,
        remove_links: bool = False,
        remove_volumes: bool = False,
    ) -> None:
        self.client.remove_container(
            self.id,
            force=force,
            remove_links=remove_links,
            remove_volumes=remove_volumes,
        )

    def logs(self, tail: str | int = "all") -> str:
        return self.client.get_container_logs(self.id, tail=tail)

    def exec(
        self, command: str | list[str], detach: bool = False, tty: bool = False
    ) -> str:
        return self.client.execute_command(self.id, command, detach=detach, tty=tty)

    def copy_to(self, source_path: str, destination_path: str) -> None:
        self.client.copy_to_container(self.id, source_path, destination_path)

    def copy_from(self, source_path: str, destination_path: str) -> None:
        self.client.copy_from_container(self.id, source_path, destination_path)

    def __repr__(self) -> str:
        return f"<Container: {self.id[:12]}>"


class Image(DockerResource):
    """A Docker image."""

    @property
    def tags(self) -> list[str]:
        return self.attrs.get("RepoTags") or []

    def remove(self, force: bool = False) -> None:
        self.client.remove_image(self.id, force=force)

    def push(
        self, tag: str | None = None, auth_config: dict[str, str] | None = None
    ) -> Generator[dict[str, Any], None, None]:
        # If tag is none, use first tag? Or raise error?
        # For object-oriented, usually image.push() pushes the image.
        # But push takes a name/tag.
        image_name = tag or (self.tags[0] if self.tags else self.id)
        if ":" in image_name:
            repo, tag_part = image_name.split(":", 1)
            yield from self.client.push_image(
                repo, tag=tag_part, auth_config=auth_config
            )
        else:
            yield from self.client.push_image(image_name, auth_config=auth_config)

    def __repr__(self) -> str:
        return f"<Image: {self.tags[0] if self.tags else self.id[:12]}>"


class Network(DockerResource):
    """A Docker network."""

    @property
    def name(self) -> str:
        return self.attrs.get("Name", "")

    @property
    def driver(self) -> str:
        return self.attrs.get("Driver", "")

    def remove(self) -> None:
        self.client.remove_network(self.id)

    def inspect(self) -> dict[str, Any]:
        return self.client.inspect_network(self.id)  # type: ignore

    def __repr__(self) -> str:
        return f"<Network: {self.name}>"


class Volume(DockerResource):
    """A Docker volume."""

    @property
    def name(self) -> str:
        return self.attrs.get("Name", "")

    @property
    def driver(self) -> str:
        return self.attrs.get("Driver", "")

    def remove(self, force: bool = False) -> None:
        self.client.remove_volume(self.name, force=force)

    def inspect(self) -> dict[str, Any]:
        return self.client.inspect_volume(self.name)  # type: ignore

    def __repr__(self) -> str:
        return f"<Volume: {self.name}>"
