import logging
from typing import Any, Dict, Generator, List

logger = logging.getLogger(__name__)


class DockerProgress:
    """
    Handles Docker progress events from generators and displays a progress bar.
    """

    def __init__(self, generator: Generator[Dict[str, Any], None, None]) -> None:
        self.generator = generator
        # Track progress of multiple layers/items by ID
        self.layers: Dict[str, Dict[str, Any]] = {}

    def consume(self) -> List[Dict[str, Any]]:
        """
        Consumes the generator, updates current progress to stdout, and returns the collected events.
        """
        events: List[Dict[str, Any]] = []
        try:
            for event in self.generator:
                events.append(event)
                self._handle_event(event)

        except Exception:
            raise

        return events

    def _handle_event(self, event: Dict[str, Any]) -> None:
        """
        Process a single event and update the display.
        """
        # Handle "stream" (e.g., from build output)
        if "stream" in event:
            # logger.info adds a newline, so we might want to strip one from the stream if present
            # so we don't double-space log output.
            msg = event["stream"].rstrip()
            if msg:
                logger.info(msg)
            return

        # Handle "status" (e.g., pull/push events)
        if "status" in event:
            layer_id = event.get("id")
            status = event["status"]
            progress = event.get("progress", "")

            if layer_id:
                # Store layer state
                prev_event = self.layers.get(layer_id)
                prev_status = prev_event.get("status") if prev_event else None

                # Only print if status changes to avoid spamming the console
                # with download progress percentages thousands of times
                if status != prev_status:
                    if progress and "Downloading" in status:
                        # Maybe modify output to show simplified intent?
                        pass

                    # Print: "ID: Status"
                    logger.info(f"{layer_id}: {status}")

                self.layers[layer_id] = event
            else:
                # Global status update
                logger.info(status)

        # Handle "error"
        if "error" in event:
            logger.info(f"Error: {event['error']}")

        # Handle "aux" (e.g., build ID)
        if "aux" in event and "ID" in event["aux"]:
            logger.info(f"ID: {event['aux']['ID']}")
