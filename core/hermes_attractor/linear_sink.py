"""Linear event sink for pipeline events.

Bridges the Attractor pipeline engine's event stream to Linear issues
using the existing linear_sync module. When a LinearSync instance is
provided, events are posted as comments on the Linear issue and the
issue state is updated at key lifecycle points.

Supports two modes:
1. Event-based: receives PipelineEvent objects from the Attractor engine
   (for use with on_event callback).
2. Simple string-based: receives (event_type, data) tuples (for use
   with the HermesPipelineRunner).

Usage::

    from core.linear_sync import LinearSync
    sync = LinearSync(api_key="...", team_id="...")
    sink = LinearSink(linear_sync=sync, issue_id="BEA-117")
    sink.on_event("pipeline.started", {"pipeline": "CredentialBroker"})
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Union

from core.linear_sync import LinearSync

log = logging.getLogger(__name__)

# Try to import PipelineEvent types for event-based mode
try:
    from attractor_pipeline.engine.events import (
        PipelineStarted,
        PipelineCompleted,
        PipelineFailed,
        StageStarted,
        StageCompleted,
        StageFailed,
        StageRetrying,
        InterviewStarted,
        InterviewCompleted,
        CheckpointSaved,
    )
    HAS_EVENTS = True
except ImportError:
    HAS_EVENTS = False


class LinearSink:
    """Event listener that publishes pipeline events to Linear.

    Follows the Observer pattern: the pipeline runner calls on_event()
    for each significant lifecycle event, and the sink publishes to
    the Linear issue (comments + state transitions) and logs locally.

    If no linear_sync is provided, events are only logged to stdout
    (useful for testing and dry runs).
    """

    # Map pipeline event types to Linear state transitions
    STATE_MAP: dict[str, str] = {
        "pipeline.started": "In Progress",
        "pipeline.completed": "Done",
        "pipeline.failed": "In Review",
    }

    # Map node completion events to Linear state transitions
    NODE_STATE_MAP: dict[str, str] = {
        "review": "In Review",
    }

    def __init__(
        self,
        linear_sync: LinearSync | None = None,
        issue_id: str | None = None,
    ) -> None:
        """Initialize the Linear sink.

        Args:
            linear_sync: Optional LinearSync instance for real API calls.
                If None, events are only logged to stdout (dry-run mode).
            issue_id: Optional Linear issue ID (e.g., "BEA-117") to
                associate events with. Required for posting comments.
        """
        self._linear_sync = linear_sync
        self._issue_id = issue_id
        self._event_log: list[dict[str, Any]] = []
        self._stage_times: dict[str, float] = {}

    def on_event(self, event_or_type: Any, data: dict[str, Any] | None = None) -> None:
        """Handle a pipeline event.

        Accepts either:
        - A PipelineEvent object (event-based mode from Attractor engine)
        - A (event_type: str, data: dict) pair (simple mode from runner)

        Args:
            event_or_type: Either a PipelineEvent object or an event type string.
            data: Event data (only used in simple string mode).
        """
        # Event-based mode: PipelineEvent object
        if HAS_EVENTS and isinstance(event_or_type, (
            PipelineStarted, PipelineCompleted, PipelineFailed,
            StageStarted, StageCompleted, StageFailed,
            StageRetrying, InterviewStarted, InterviewCompleted,
            CheckpointSaved,
        )):
            self._handle_pipeline_event(event_or_type)
            return

        # Simple string mode: (event_type, data) tuple
        event_type = event_or_type
        data = data or {}
        self._handle_simple_event(event_type, data)

    def _handle_pipeline_event(self, event: Any) -> None:
        """Handle a PipelineEvent object from the Attractor engine."""
        timestamp = datetime.now(timezone.utc).isoformat()

        if isinstance(event, PipelineStarted):
            self._post(f"🚀 **Pipeline started**: {event.name} (id={event.id})")
            self._update_state("In Progress")

        elif isinstance(event, StageStarted):
            self._stage_times[event.name] = time.monotonic()

        elif isinstance(event, StageCompleted):
            duration = time.monotonic() - self._stage_times.get(event.name, time.monotonic())
            self._post(f"✅ **Stage completed**: {event.name} in {duration:.1f}s")

        elif isinstance(event, StageFailed):
            retry_text = " (will retry)" if event.will_retry else ""
            self._post(f"❌ **Stage failed**: {event.name} — {event.error}{retry_text}")

        elif isinstance(event, StageRetrying):
            self._post(f"🔄 **Retrying**: {event.name} (attempt {event.attempt})")

        elif isinstance(event, InterviewStarted):
            self._post(f"🤔 **Human gate**: {event.stage} — {event.question}")
            self._update_state("In Review")

        elif isinstance(event, InterviewCompleted):
            self._post(f"👤 **Human responded**: {event.answer} ({event.duration:.1f}s)")

        elif isinstance(event, PipelineCompleted):
            self._post(f"🎉 **Pipeline completed** in {event.duration:.1f}s ({event.artifact_count} artifacts)")
            self._update_state("Done")

        elif isinstance(event, PipelineFailed):
            self._post(f"💥 **Pipeline failed** after {event.duration:.1f}s: {event.error}")
            self._update_state("In Review")

        elif isinstance(event, CheckpointSaved):
            log.debug(f"Checkpoint saved at {event.node_id}")

    def _handle_simple_event(self, event_type: str, data: dict[str, Any]) -> None:
        """Handle a simple (event_type, data) pair from the runner."""
        timestamp = datetime.now(timezone.utc).isoformat()

        event_record = {
            "timestamp": timestamp,
            "event_type": event_type,
            "issue_id": self._issue_id,
            **data,
        }
        self._event_log.append(event_record)

        # Format and post to Linear
        message = self._format_event(event_type, data, timestamp)
        self._post(message)

        # Update issue state if there's a mapping
        new_state = self.STATE_MAP.get(event_type)
        if new_state:
            self._update_state(new_state)

        # Check for node-level state transitions
        node_id = data.get("node_id")
        if not node_id and isinstance(data.get("completed_nodes"), list):
            completed = data["completed_nodes"]
            if completed:
                node_id = completed[-1]
        if node_id and node_id in self.NODE_STATE_MAP:
            self._update_state(self.NODE_STATE_MAP[node_id])

        # Also log to stdout
        print(
            f"[LINEAR SINK] {event_type} | "
            f"issue={self._issue_id or 'N/A'} | "
            f"{json.dumps(data, default=str)}"
        )

    def _post(self, message: str) -> None:
        """Post a comment to the Linear issue."""
        if self._linear_sync and self._issue_id:
            try:
                self._linear_sync.post_event(self._issue_id, message)
            except Exception as e:
                log.warning(f"Linear comment failed: {e}")

    def _update_state(self, state_name: str) -> None:
        """Update the Linear issue state."""
        if self._linear_sync and self._issue_id:
            try:
                self._linear_sync.update_issue_state(self._issue_id, state_name)
            except Exception as e:
                log.warning(f"Linear state update failed: {e}")

    def _format_event(self, event_type: str, data: dict[str, Any], timestamp: str) -> str:
        """Format a pipeline event as a Linear comment."""
        emoji_map = {
            "pipeline.started": "🚀",
            "pipeline.completed": "✅",
            "pipeline.failed": "❌",
            "node.completed": "⚙️",
            "node.started": "▶️",
        }
        emoji = emoji_map.get(event_type, "📌")

        lines = [f"{emoji} **{event_type}** — _{timestamp}_"]

        for key, value in data.items():
            if key == "completed_nodes" and isinstance(value, list):
                lines.append(f"- **Nodes**: {' → '.join(value)}")
            elif key == "duration_seconds":
                lines.append(f"- **Duration**: {value}s")
            elif key == "error" and value:
                lines.append(f"- **Error**: {value}")
            elif key not in ("completed_nodes", "error"):
                lines.append(f"- **{key}**: {value}")

        return "\n".join(lines)

    @property
    def event_log(self) -> list[dict[str, Any]]:
        """Return the accumulated event log."""
        return list(self._event_log)