"""Hermes-backed CodergenBackend for the Attractor pipeline.

Routes codergen nodes to Hermes delegate_task via the CLI. Each node
becomes a Hermes subagent call with the node's prompt expanded with
context variables and model routing from the stylesheet.

For nodes with class="frontier" or explicit llm_model/llm_provider,
the model is routed accordingly via node attributes (set by the
stylesheet applicator).

When the Hermes CLI is not available, falls back to a dry-run mode
that returns the delegation payload as structured JSON.
"""

from __future__ import annotations

import json
import logging
import subprocess
from typing import Any

from attractor_pipeline.engine.runner import HandlerResult, Outcome
from attractor_pipeline.graph import Node

log = logging.getLogger(__name__)


class HermesCodergenBackend:
    """CodergenBackend that delegates LLM work to Hermes via CLI.

    Tries to call `hermes delegate` for real subagent execution.
    Falls back to a dry-run JSON payload if the CLI is unavailable.
    """

    def __init__(
        self,
        *,
        workdir: str | None = None,
        default_model: str = "claude-sonnet-4-5",
        default_provider: str | None = None,
        dry_run: bool = False,
    ) -> None:
        self._workdir = workdir
        self._default_model = default_model
        self._default_provider = default_provider
        self._dry_run = dry_run

    async def run(
        self,
        node: Node,
        prompt: str,
        context: dict[str, Any],
        abort_signal: Any | None = None,
    ) -> str | HandlerResult:
        """Execute a codergen node by delegating to a Hermes subagent.

        Args:
            node: The pipeline node being executed.
            prompt: The expanded prompt text for this node.
            context: The current pipeline context dictionary.
            abort_signal: Cooperative cancellation signal.

        Returns:
            HandlerResult with status=SUCCESS containing the delegation
            result, or status=FAIL on error.
        """
        import asyncio

        # Resolve model and provider from node attrs (stylesheet-applied)
        model = node.llm_model or self._default_model
        provider = node.llm_provider or self._default_provider

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

        # Delegate synchronously via thread pool
        result_text = await asyncio.to_thread(
            self._delegate_sync, goal, model, provider, toolsets
        )
        if result_text.startswith("[Error:"):
            return HandlerResult(
                status=Outcome.FAIL,
                failure_reason=result_text,
                output=result_text,
            )

        return result_text

    def _delegate_sync(
        self,
        goal: str,
        model: str,
        provider: str | None,
        toolsets: list[str],
    ) -> str:
        """Synchronous delegation — calls Hermes delegate via CLI.

        Returns the subagent's output text on success, or an error
        string prefixed with [Error:] on failure. In dry-run mode,
        returns a structured JSON payload without calling the CLI.
        """
        # Dry-run mode: return JSON payload without calling CLI
        if self._dry_run:
            payload = {
                "type": "hermes_delegation",
                "model": model,
                "provider": provider,
                "toolsets": toolsets,
                "goal": goal[:2000],
                "dry_run": True,
            }
            log.info(f"[HERMES DELEGATION] dry-run: node model={model}")
            return json.dumps(payload, indent=2)

        # Try hermes CLI first
        try:
            cmd = [
                "hermes", "delegate",
                "--goal", goal,
                "--toolsets", ",".join(toolsets),
            ]
            if model:
                cmd.extend(["--model", model])
            if provider:
                cmd.extend(["--provider", provider])
            if self._workdir:
                cmd.extend(["--workdir", self._workdir])

            log.info(f"[HERMES DELEGATION] goal={goal[:100]}... model={model}")
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=600
            )
            if result.returncode == 0:
                return result.stdout.strip()
            return f"[Error: delegate_task returned {result.returncode}: {result.stderr[:500]}]"

        except FileNotFoundError:
            # hermes CLI not available — dry run mode
            log.warning("[HERMES DELEGATION] CLI not available, using dry-run mode")
            payload = {
                "type": "hermes_delegation",
                "model": model,
                "provider": provider,
                "toolsets": toolsets,
                "goal": goal[:2000],
                "dry_run": True,
            }
            return json.dumps(payload, indent=2)

        except subprocess.TimeoutExpired:
            return "[Error: delegate_task timed out after 600s]"