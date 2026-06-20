"""Hermes Bridge — integrates samueljklee/attractor pipeline with Hermes agent.

Three integration points:
1. HermesCodergenBackend: Routes codergen nodes to Hermes delegate_task
2. HermesInterviewer: Routes human-gate nodes to Hermes clarify()
3. LinearEventSink: Subscribes to pipeline events and posts to Linear

Usage:
    from hermes_bridge import (
        HermesCodergenBackend,
        HermesInterviewer,
        LinearEventSink,
        build_registry,
        run_feature_pipeline,
    )
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

# attractor imports
from attractor_pipeline.engine.runner import HandlerRegistry, HandlerResult, Outcome
from attractor_pipeline.graph import Graph, Node
from attractor_pipeline.handlers import (
    AutoApproveInterviewer,
    CodergenBackend,
    Interviewer,
    Question,
    Answer,
    QuestionType,
    register_default_handlers,
)
from attractor_pipeline.parser import parse_dot
from attractor_pipeline.validation import validate

# Event types for the sink
from attractor_pipeline.engine.events import (
    PipelineEvent,
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

log = logging.getLogger("hermes_bridge")


# ------------------------------------------------------------------ #
# LinearSync — adapted from our existing linear_sync.py
# ------------------------------------------------------------------ #

class LinearSync:
    """Posts pipeline events as comments on Linear issues."""

    def __init__(self, api_key: str | None = None, team_id: str | None = None):
        env = dotenv_values(Path(__file__).parent.parent.parent / ".hermes" / "profiles" / "mac" / ".env")
        self.api_key = api_key or env.get("LINEAR_API_KEY", "")
        self.team_id = team_id or env.get("LINEAR_TEAM_ID", "")
        self.url = "https://api.linear.app/graphql"
        self.headers = {
            "Authorization": self.api_key,
            "Content-Type": "application/json",
        }

    def post_comment(self, issue_id: str, body: str) -> dict:
        """Post a comment to a Linear issue."""
        query = """
        mutation CreateComment($issueId: String!, $body: String!) {
            createComment(input: {issueId: $issueId, body: $body}) {
                success
            }
        }
        """
        variables = {"issueId": issue_id, "body": body}
        try:
            import requests
            resp = requests.post(self.url, headers=self.headers, json={"query": query, "variables": variables})
            return resp.json()
        except Exception as e:
            log.warning(f"Linear comment failed: {e}")
            return {"error": str(e)}

    def update_state(self, issue_id: str, state_name: str) -> dict:
        """Move a Linear issue to a new state."""
        state_id = self._get_state_id(state_name)
        if not state_id:
            return {"error": f"State '{state_name}' not found"}
        query = """
        mutation UpdateIssue($id: String!, $stateId: String!) {
            updateIssue(input: {id: $id, stateId: $stateId}) {
                success
            }
        }
        """
        variables = {"id": issue_id, "stateId": state_id}
        try:
            import requests
            resp = requests.post(self.url, headers=self.headers, json={"query": query, "variables": variables})
            return resp.json()
        except Exception as e:
            log.warning(f"Linear state update failed: {e}")
            return {"error": str(e)}

    def _get_state_id(self, state_name: str) -> str | None:
        query = """
        query {
            issueStates {
                nodes { name id }
            }
        }
        """
        try:
            import requests
            resp = requests.post(self.url, headers=self.headers, json={"query": query})
            data = resp.json()
            for state in data["data"]["issueStates"]["nodes"]:
                if state["name"].lower() == state_name.lower():
                    return state["id"]
        except Exception:
            pass
        return None


# ------------------------------------------------------------------ #
# HermesCodergenBackend
# ------------------------------------------------------------------ #

class HermesCodergenBackend:
    """Routes codergen nodes to Hermes delegate_task.

    Each codergen node becomes a Hermes subagent call. The node's prompt
    is expanded with context variables (goal, prior outputs) and sent
    as the subagent's goal. The subagent works in the project repo
    directory and returns a summary.

    For nodes with class="frontier" or explicit llm_model/llm_provider,
    the model is routed accordingly via the node attributes.
    """

    def __init__(self, workdir: str | None = None):
        self.workdir = workdir or os.getcwd()

    async def run(
        self,
        node: Node,
        prompt: str,
        context: dict[str, Any],
        abort_signal: Any | None = None,
    ) -> str | HandlerResult:
        """Execute a codergen node by delegating to a Hermes subagent."""
        import asyncio

        # Build the subagent goal from the expanded prompt
        goal = prompt
        if context.get("goal"):
            goal = f"{prompt}\n\nProject context: {context['goal']}"

        # Include prior codergen outputs as context
        prior_outputs = []
        for key, value in sorted(context.items()):
            if key.startswith("codergen.") and key.endswith(".output") and value:
                stage_name = key.replace("codergen.", "").replace(".output", "")
                prior_outputs.append(f"[{stage_name}]:\n{value[:2000]}")

        if prior_outputs:
            goal += "\n\nPrior stage outputs:\n" + "\n---\n".join(prior_outputs)

        # Determine toolsets based on node class
        toolsets = ["terminal", "file", "web"]
        node_class = node.attrs.get("class", "")
        if node_class == "frontier":
            toolsets.append("delegation")

        # Build delegate_task command
        # We use Hermes CLI if available, otherwise fall back to direct execution
        result_text = await asyncio.to_thread(
            self._delegate_sync, goal, node, toolsets
        )

        if result_text.startswith("[Error:"):
            return HandlerResult(
                status=Outcome.FAIL,
                failure_reason=result_text,
                output=result_text,
            )

        return result_text

    def _delegate_sync(self, goal: str, node: Node, toolsets: list[str]) -> str:
        """Synchronous delegation — calls Hermes delegate_task via CLI.

        In practice, when running inside Hermes, the pipeline runner
        will use delegate_task directly. This CLI path is for standalone runs.
        """
        # Try hermes CLI first
        try:
            cmd = [
                "hermes", "delegate",
                "--goal", goal,
                "--toolsets", ",".join(toolsets),
            ]
            if node.llm_model:
                cmd.extend(["--model", node.llm_model])
            if self.workdir:
                cmd.extend(["--workdir", self.workdir])

            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=600
            )
            if result.returncode == 0:
                return result.stdout.strip()
            return f"[Error: delegate_task returned {result.returncode}: {result.stderr[:500]}]"
        except FileNotFoundError:
            # hermes CLI not available — return the prompt for dry-run
            return f"[Dry run — no Hermes CLI] Goal: {goal[:500]}"
        except subprocess.TimeoutExpired:
            return "[Error: delegate_task timed out after 600s]"


# ------------------------------------------------------------------ #
# HermesInterviewer
# ------------------------------------------------------------------ #

class HermesInterviewer:
    """Routes human-gate questions to Hermes clarify().

    When running inside Hermes, this calls the clarify tool.
    When running standalone, it falls back to console input.
    """

    def __init__(self, issue_id: str | None = None, linear_sync: LinearSync | None = None):
        self.issue_id = issue_id
        self.linear_sync = linear_sync

    async def ask(self, question: Question) -> Answer:
        """Ask a human via Hermes clarify or console fallback."""
        import asyncio

        # Post to Linear if configured
        if self.linear_sync and self.issue_id:
            self.linear_sync.post_comment(
                self.issue_id,
                f"🤔 **Human Gate** (`{question.stage}`):\n{question.text}"
                + (f"\nOptions: {', '.join(question.options)}" if question.options else ""),
            )

        # Try Hermes clarify CLI
        try:
            cmd = ["hermes", "clarify", "--question", question.text]
            if question.options:
                cmd.extend(["--choices", ",".join(question.options)])

            result = await asyncio.to_thread(
                subprocess.run, cmd, capture_output=True, text=True, timeout=3600
            )
            if result.returncode == 0:
                value = result.stdout.strip()
                selected = value if (question.options and value in question.options) else None
                return Answer(value=value, selected_option=selected, text=value)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        # Console fallback
        prompt_text = f"\n[HUMAN GATE: {question.stage}]\n{question.text}"
        if question.options:
            prompt_text += f"\nOptions: {', '.join(question.options)}"
        prompt_text += "\n> "

        value = await asyncio.to_thread(input, prompt_text)
        selected = value if (question.options and value in question.options) else None
        return Answer(value=value, selected_option=selected, text=value)


# ------------------------------------------------------------------ #
# LinearEventSink
# ------------------------------------------------------------------ #

class LinearEventSink:
    """Subscribes to pipeline events and posts them to Linear."""

    def __init__(self, issue_id: str, linear_sync: LinearSync | None = None):
        self.issue_id = issue_id
        self.linear_sync = linear_sync or LinearSync()
        self._stage_times: dict[str, float] = {}

    def on_event(self, event: PipelineEvent) -> None:
        """Handle a pipeline event by posting to Linear."""
        import time

        if isinstance(event, PipelineStarted):
            self.linear_sync.post_comment(
                self.issue_id,
                f"🚀 **Pipeline started**: {event.name} (id={event.id})",
            )

        elif isinstance(event, StageStarted):
            self._stage_times[event.name] = time.monotonic()

        elif isinstance(event, StageCompleted):
            self.linear_sync.post_comment(
                self.issue_id,
                f"✅ **Stage completed**: {event.name} in {event.duration:.1f}s",
            )

        elif isinstance(event, StageFailed):
            retry_text = " (will retry)" if event.will_retry else ""
            self.linear_sync.post_comment(
                self.issue_id,
                f"❌ **Stage failed**: {event.name} — {event.error}{retry_text}",
            )

        elif isinstance(event, StageRetrying):
            self.linear_sync.post_comment(
                self.issue_id,
                f"🔄 **Retrying**: {event.name} (attempt {event.attempt})",
            )

        elif isinstance(event, InterviewStarted):
            self.linear_sync.post_comment(
                self.issue_id,
                f"🤔 **Human gate**: {event.stage} — {event.question}",
            )

        elif isinstance(event, InterviewCompleted):
            self.linear_sync.post_comment(
                self.issue_id,
                f"👤 **Human responded**: {event.answer} ({event.duration:.1f}s)",
            )

        elif isinstance(event, PipelineCompleted):
            self.linear_sync.post_comment(
                self.issue_id,
                f"🎉 **Pipeline completed** in {event.duration:.1f}s ({event.artifact_count} artifacts)",
            )
            self.linear_sync.update_state(self.issue_id, "Done")

        elif isinstance(event, PipelineFailed):
            self.linear_sync.post_comment(
                self.issue_id,
                f"💥 **Pipeline failed** after {event.duration:.1f}s: {event.error}",
            )
            self.linear_sync.update_state(self.issue_id, "In Progress")

        elif isinstance(event, CheckpointSaved):
            log.debug(f"Checkpoint saved at {event.node_id}")


# ------------------------------------------------------------------ #
# Registry builder
# ------------------------------------------------------------------ #

def build_registry(
    *,
    codergen_backend: CodergenBackend | None = None,
    interviewer: Interviewer | None = None,
) -> HandlerRegistry:
    """Build a HandlerRegistry with our backends wired in."""
    registry = HandlerRegistry()
    register_default_handlers(
        registry,
        codergen_backend=codergen_backend,
        interviewer=interviewer,
    )
    return registry


# ------------------------------------------------------------------ #
# Pipeline runner
# ------------------------------------------------------------------ #

async def run_feature_pipeline(
    dot_path: str | Path,
    *,
    issue_id: str | None = None,
    workdir: str | None = None,
    context: dict[str, Any] | None = None,
    dry_run: bool = False,
) -> Any:
    """Run a feature implementation pipeline from a DOT file.

    This is the main entry point for the AI Software Factory.

    Args:
        dot_path: Path to the DOT pipeline spec.
        issue_id: Linear issue ID (e.g., "BEA-117") for event posting.
        workdir: Working directory for subagent tasks.
        context: Initial context variables.
        dry_run: If True, use AutoApproveInterviewer and don't call LLMs.

    Returns:
        PipelineResult with final status and context.
    """
    import anyio

    # Parse and validate the DOT file
    dot_text = Path(dot_path).read_text()
    graph = parse_dot(dot_text)

    issues = validate(graph)
    if issues:
        log.warning(f"Pipeline validation issues: {issues}")
        # Filter to errors only (severity >= error)
        errors = [i for i in issues if hasattr(i, 'severity') and str(i.severity) in ('error', '3')]
        if errors:
            raise ValueError(f"Pipeline has validation errors: {errors}")

    # Build backends
    if dry_run:
        backend = None  # CodergenHandler returns placeholder
        interviewer = AutoApproveInterviewer()
    else:
        backend = HermesCodergenBackend(workdir=workdir)
        interviewer = HermesInterviewer(
            issue_id=issue_id,
            linear_sync=LinearSync() if issue_id else None,
        )

    # Build registry
    registry = build_registry(
        codergen_backend=backend,
        interviewer=interviewer,
    )

    # Build event sink
    on_event = None
    if issue_id:
        sink = LinearEventSink(issue_id=issue_id)
        on_event = sink.on_event

    # Prepare context
    ctx = dict(context or {})

    # Run the pipeline
    result = await run_pipeline(
        graph,
        registry,
        context=ctx,
        on_event=on_event,
    )

    return result


# Need to import run_pipeline at module level for the async function
from attractor_pipeline.engine.runner import run_pipeline