import base64
import io
import json
import logging
import tarfile
import urllib.parse
from typing import TYPE_CHECKING, Any, Generator

from .docker_resource import DockerResource
from .transport import UnixHttpConnection

if TYPE_CHECKING:
    from .client import DockerClient

logger = logging.getLogger(__name__)


class Image(DockerResource):
    """A Docker image."""

    @classmethod
    def list(cls, client: "DockerClient", show_all: bool = False) -> list["Image"]:
        """
        List images.
        Equivalent to: docker images
        """
        params = {"all": "true"} if show_all else {}
        query = urllib.parse.urlencode(params)
        data = client._request("GET", f"/images/json?{query}")
        return [cls(client, image_data) for image_data in data]

    @classmethod
    def pull(
        cls, client: "DockerClient", image_name: str
    ) -> Generator[dict[str, Any], None, None]:
        """
        Pull an image.
        Equivalent to: docker pull

        Yields:
            dict: Progress objects from Docker
        """
        logger.info("Pulling %s...", image_name)

        conn = UnixHttpConnection(client.socket_path)
        # Note: We manually handle the connection here to use our stream helper
        # Instead of client._request which reads the whole body
        conn.request("POST", f"/images/create?fromImage={image_name}")
        response = conn.getresponse()

        yield from client._stream_json_response(response)

    @classmethod
    def build(
        cls, client: "DockerClient", path: str, tag: str
    ) -> Generator[dict[str, Any], None, None]:
        """
        Build an image from a directory.
        Equivalent to: docker build -t tag path

        Yields:
            dict: Build progress objects
        """
        logger.info("Building image %s from %s...", tag, path)

        # Create a tar context
        tar_stream = io.BytesIO()
        with tarfile.open(fileobj=tar_stream, mode="w") as tar:
            tar.add(path, arcname=".")
        tar_stream.seek(0)

        query = urllib.parse.urlencode({"t": tag})
        headers = {
            "Content-Type": "application/x-tar",
            "Content-Length": str(len(tar_stream.getvalue())),
            "Connection": "close",
        }

        conn = UnixHttpConnection(client.socket_path)
        logger.debug("POST /build?%s", query)
        conn.request(
            "POST", f"/build?{query}", body=tar_stream.getvalue(), headers=headers
        )
        response = conn.getresponse()

        yield from client._stream_json_response(response)

    @property
    def tags(self) -> list[str]:
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

    def push(
        self, tag: str | None = None, auth_config: dict[str, str] | None = None
    ) -> Generator[dict[str, Any], None, None]:
        """
        Push an image.
        Equivalent to: docker push
        """
        # If tag is none, use first tag? Or raise error?
        image_name = tag or (self.tags[0] if self.tags else self.resource_id)

        # If the user passed a tag to push() intended as the image name to push
        # we need to be careful. The original implementation split by colon.
        # But here we are calling push on an Image object.
        # The original `push` method on `Image` called `client.push_image`.
        # `client.push_image` took `image` (string).

        # Logic from client.push_image:
        if tag and ":" not in image_name:
            # Since image_name was set to tag if tag was present...
            # The original client.push_image(image, tag) logic:
            # if tag: image = f"{image}:{tag}"
            pass

        # Actually I should implement the logic from client.push_image here.
        # The logic below is adapted.

        target_image = image_name

        # Re-implementing client.push_image logic
        # Note: client.push_image(image: str, tag: str | None)
        # If I call image.push(tag="latest"), does it mean push this image as "latest"?
        # Or push the tag "latest" of this image?
        # The Models.py Image.push implementation was:
        # yield from self.client.push_image(repo, tag=tag_part) if ":" in image_name

        return self._push_image_logic(
            self.client, target_image, auth_config=auth_config
        )

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
