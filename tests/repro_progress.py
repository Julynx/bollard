import os
import sys

sys.path.insert(0, os.path.abspath("src"))

import json
import logging

from bollard import DockerClient

# Configure logging to see debug output if needed
logging.basicConfig(level=logging.INFO)


def run():
    try:
        client = DockerClient()
        print("--- Testing default pull (Progress Bar) ---")
        # specific tag to force pull if possible, or just latest
        img = client.pull_image("alpine:latest")
        print(f"Result: {img}")

        print("\n--- Testing progress=True (Generator) ---")
        gen = client.pull_image("alpine:latest", progress=True)
        for item in gen:
            print(f"EVENT: {json.dumps(item)}")
    except Exception as e:
        print(f"Error: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    run()
