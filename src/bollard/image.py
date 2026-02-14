import base64
import json
import logging
import os
import tarfile
import tempfile
import urllib.parse
from typing import TYPE_CHECKING, Any, Generator, List, Literal, overload

from .docker_resource import DockerResource
from .ignore import DockerIgnore
from .transport import UnixHttpConnection

if TYPE_CHECKING:
    from .client import DockerClient

logger = logging.getLogger(__name__)


class Image(DockerResource):
    """A Docker image."""

    @classmethod
    def list(cls, client: "DockerClient", show_all: bool = False) -> List["Image"]:
        """
        List images.
        Equivalent to: docker images
        """
        params = {"all": "true"} if show_all else {}
        query = urllib.parse.urlencode(params)
        data = client._request("GET", f"/images/json?{query}")
        return [cls(client, image_data) for image_data in data]

    @classmethod
    @overload
    def pull(
        cls, client: "DockerClient", image_name: str, progress: Literal[True]
    ) -> Generator[dict[str, Any], None, None]: ...

    @classmethod
    @overload
    def pull(
        cls, client: "DockerClient", image_name: str, progress: Literal[False] = False
    ) -> "Image": ...

    @classmethod
    def pull(
        cls, client: "DockerClient", image_name: str, progress: bool = False
    ) -> Generator[dict[str, Any], None, None] | "Image":
        """
        Pull an image.
        Equivalent to: docker pull

        Args:
            client: The DockerClient instance.
            image_name: The name of the image to pull.
            progress: If True, yields progress objects. If False (default),
                      displays a progress bar and returns the Image object.

        Returns:
            Image object (if progress=False) or Generator (if progress=True)
        """
        logger.info("Pulling %s...", image_name)

        conn = UnixHttpConnection(client.socket_path)
        conn.request("POST", f"/images/create?fromImage={image_name}")
        response = conn.getresponse()

        generator = client._stream_json_response(response)

        if progress:
            return generator

        from .progress import DockerProgress

        DockerProgress(generator).consume()
        return cls(client, {"Id": image_name})

    @classmethod
    @overload
    def build(
        cls, client: "DockerClient", path: str, tag: str, progress: Literal[True]
    ) -> Generator[dict[str, Any], None, None]: ...

    @classmethod
    @overload
    def build(
        cls,
        client: "DockerClient",
        path: str,
        tag: str,
        progress: Literal[False] = False,
    ) -> "Image": ...

    @classmethod
    def build(
        cls, client: "DockerClient", path: str, tag: str, progress: bool = False
    ) -> Generator[dict[str, Any], None, None] | "Image":
        """
        Build an image from a directory.
        Equivalent to: docker build -t tag path

        Args:
            client: The DockerClient instance.
            path: Path to the directory containing Dockerfile.
            tag: Tag to apply to the built image.
            progress: If True, yields progress objects. If False (default),
                      displays a progress bar and returns the Image object.
        """
        logger.info("Building image %s from %s...", tag, path)

        docker_ignore = DockerIgnore(path)

        temp_context = tempfile.TemporaryFile()
        try:
            with tarfile.open(fileobj=temp_context, mode="w") as tar:
                for root, dirs, files in os.walk(path):
                    dirs[:] = [
                        dir_name
                        for dir_name in dirs
                        if not docker_ignore.is_ignored(os.path.join(root, dir_name))
                    ]

                    for file in files:
                        full_path = os.path.join(root, file)
                        rel_path = os.path.relpath(full_path, path)

                        if not docker_ignore.is_ignored(rel_path):
                            tar.add(full_path, arcname=rel_path)

            temp_context.seek(0)

            query = urllib.parse.urlencode({"t": tag})
            headers = {
                "Content-Type": "application/x-tar",
                "Connection": "keep-alive",
            }
            response = client._request(
                "POST",
                f"/build?{query}",
                body=temp_context,
                headers=headers,
                stream=True,
            )

            generator = client._stream_json_response(response)

            if progress:
                return generator

            from .progress import DockerProgress

            events = DockerProgress(generator).consume()

            image_id = tag
            for event in reversed(events):
                if "aux" in event and "ID" in event["aux"]:
                    image_id = event["aux"]["ID"]
                    break

            return cls(client, {"Id": image_id, "RepoTags": [tag]})
        finally:
            temp_context.close()

    @property
    def tags(self) -> List[str]:
        return self.attrs.get("RepoTags") or []

    def remove(self, force: bool = False) -> None:
        """
        Remove an image.
        Equivalent to: docker rmi
        """
        logger.info("Removing image %s...", self.resource_id)
        params = {"force": "true"} if force else {}
        query = urllib.parse.urlencode(params)
        self.client._request("DELETE", f"/images/{self.resource_id}?{query}")

    @overload
    def push(
        self,
        tag: str | None = None,
        auth_config: dict[str, str] | None = None,
        progress: Literal[True] = ...,
    ) -> Generator[dict[str, Any], None, None]: ...

    @overload
    def push(
        self,
        tag: str | None = None,
        auth_config: dict[str, str] | None = None,
        progress: Literal[False] = False,
    ) -> List[dict[str, Any]]: ...

    def push(
        self,
        tag: str | None = None,
        auth_config: dict[str, str] | None = None,
        progress: bool = False,
    ) -> Generator[dict[str, Any], None, None] | List[dict[str, Any]]:
        """
        Push an image.
        Equivalent to: docker push

        Args:
            tag: Optional tag to push.
            auth_config: Optional authentication config.
            progress: If True, yields progress objects. If False (default),
                      displays a progress bar and returns the list of events.
        """
        image_name = tag or (self.tags[0] if self.tags else self.resource_id)

        if tag and ":" not in image_name:
            pass

        generator = self._push_image_logic(
            self.client, image_name, auth_config=auth_config
        )

        if progress:
            return generator

        from .progress import DockerProgress

        return DockerProgress(generator).consume()

    @classmethod
    def _push_image_logic(
        cls,
        client: "DockerClient",
        image: str,
        tag: str | None = None,
        auth_config: dict[str, str] | None = None,
    ) -> Generator[dict[str, Any], None, None]:
        if tag:
            image = f"{image}:{tag}"

        logger.info("Pushing %s...", image)

        # Auto-detect auth if not provided
        if auth_config is None:
            auth_config = cls._get_auth_for_image(client, image)
            if auth_config:
                logger.info("Using authenticated credentials for push.")

        headers = {}
        if auth_config:
            encoded_auth = base64.b64encode(json.dumps(auth_config).encode()).decode()
            headers["X-Registry-Auth"] = encoded_auth

        conn = UnixHttpConnection(client.socket_path)
        logger.debug("POST /images/%s/push", image)
        conn.request("POST", f"/images/{image}/push", headers=headers)
        response = conn.getresponse()

        yield from client._stream_json_response(response)

    @staticmethod
    def _get_auth_for_image(
        client: "DockerClient", image: str
    ) -> dict[str, str] | None:
        """
        Resolve authentication for a given image from the local Docker config.
        """
        parts = image.split("/", 1)
        if "." in parts[0] or ":" in parts[0] or parts[0] == "localhost":
            registry = parts[0]
        else:
            registry = "index.docker.io/v1/"

        auths = client.load_docker_config()

        # Try exact match
        if registry in auths:
            return auths[registry]  # type: ignore

        # Try with https:// prefix
        if f"https://{registry}" in auths:
            return auths[f"https://{registry}"]  # type: ignore

        # Fallback for docker hub
        if registry == "docker.io" and "index.docker.io/v1/" in auths:
            return auths["index.docker.io/v1/"]  # type: ignore

        return None

    def __repr__(self) -> str:
        return f"<Image: {self.tags[0] if self.tags else self.resource_id[:12]}>"
