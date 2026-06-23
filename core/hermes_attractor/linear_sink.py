"""Linear event sink for pipeline events.

Provides a simple event listener that logs pipeline events as structured
messages. Will be wired to the real Linear API later using the existing
linear_sync module.

Usage::

    sink = LinearSink()
    sink.on_event("pipeline.started", {"pipeline": "hello_hermes", "goal": "..."})
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


class LinearSink:
    """Event listener that logs pipeline events for Linear integration.

    For now, this prints structured event messages to stdout. The real
    Linear API calls will be wired in later using the core.linear_sync module.

    This follows the Observer pattern: the pipeline runner calls on_event()
    for each significant lifecycle event, and the sink handles publishing
    to the appropriate destination.
    """

    def __init__(self, issue_id: str | None = None) -> None:
        """Initialize the Linear sink.

        Args:
            issue_id: Optional Linear issue ID to associate events with.
                When wired to the real API, this will be used to post
                comments and update issue state.
        """
        self._issue_id = issue_id
        self._event_log: list[dict[str, Any]] = []

    def on_event(self, event_type: str, data: dict[str, Any]) -> None:
        """Handle a pipeline event by logging it as a structured message.

        Currently prints a formatted event message to stdout. Later this
        will call core.linear_sync.LinearSync.post_event() to publish
        to a Linear issue.

        Args:
            event_type: The type of event (e.g., "pipeline.started",
                "stage.completed", "stage.failed", "pipeline.completed").
            data: Event-specific data dictionary (node IDs, outputs,
                durations, error messages, etc.).
        """
        timestamp = datetime.now(timezone.utc).isoformat()

        event_record = {
            "timestamp": timestamp,
            "event_type": event_type,
            "issue_id": self._issue_id,
            **data,
        }

        # Store in local log
        self._event_log.append(event_record)

        # Print structured message (bridge point for real Linear API)
        message = (
            f"[LINEAR SINK] {event_type} | "
            f"issue={self._issue_id or 'N/A'} | "
            f"{json.dumps(data, default=str)}"
        )
        print(message)

    @property
    def event_log(self) -> list[dict[str, Any]]:
        """Return the accumulated event log."""
        return list(self._event_log)