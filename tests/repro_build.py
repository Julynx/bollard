import os
import shutil
import sys
import tempfile
import uuid

# Add src to path
sys.path.insert(0, os.path.abspath("src"))

from bollard import DockerClient


def run():
    client = DockerClient()

    # Create temp dir for Dockerfile
    tmp_path = tempfile.mkdtemp()
    try:
        dockerfile_path = os.path.join(tmp_path, "Dockerfile")
        with open(dockerfile_path, "w") as f:
            f.write("FROM alpine:latest\nRUN echo 'Built with Bollard'")

        random_name = uuid.uuid4().hex[:8]
        image_tag = f"bollard-test-{random_name}:latest"

        print(f"Building image: {image_tag} from {tmp_path}")

        # Test default build (progress=False)
        img = client.build_image(tmp_path, image_tag)
        print(f"Build result: {img}")

        # Verify
        images = client.list_images()
        found = False
        print(f"Listing {len(images)} images...")
        for i in images:
            print(f"ID: {i.resource_id}, Tags: {i.tags}")
            if image_tag in i.tags:
                found = True
                break

        if found:
            print("SUCCESS: Image found in list.")
        else:
            print("FAILURE: Image NOT found in list.")

        # Clean up image
        try:
            client.remove_image(image_tag)
        except Exception as e:
            print(f"Error removing image: {e}")

    finally:
        shutil.rmtree(tmp_path)


if __name__ == "__main__":
    run()
