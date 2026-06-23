"""Hermes-backed CodergenBackend for the Attractor pipeline.

Instead of calling a raw LLM API, this backend constructs a structured
delegation payload for Hermes to process later. This bridges the Attractor
pipeline engine to Hermes' task delegation system.

Usage::

    backend = HermesCodergenBackend()
    handler = CodergenHandler(backend=backend)
    registry.register("codergen", handler)
"""

from __future__ import annotations

import json
from typing import Any

from attractor_pipeline.engine.runner import HandlerResult, Outcome
from attractor_pipeline.graph import Node


class HermesCodergenBackend:
    """CodergenBackend that delegates LLM work to Hermes.

    Constructs a structured JSON delegation payload from node attributes
    and context, suitable for Hermes to pick up and execute asynchronously.

    Since we can't call Hermes delegate_task directly from Python yet,
    this backend prints the delegation message and returns a
    HandlerResult with status=SUCCESS containing the payload.
    """

    def __init__(
        self,
        *,
        default_model: str = "claude-sonnet-4-5",
        default_provider: str | None = None,
    ) -> None:
        self._default_model = default_model
        self._default_provider = default_provider

    async def run(
        self,
        node: Node,
        prompt: str,
        context: dict[str, Any],
        abort_signal: Any | None = None,
    ) -> str | HandlerResult:
        """Build a delegation payload for Hermes to execute.

        Reads node attributes (model, provider, goal_gate, etc.) and
        constructs a structured JSON payload. The payload is printed
        to stdout and returned as a HandlerResult.

        Args:
            node: The pipeline node being executed.
            prompt: The expanded prompt text for this node.
            context: The current pipeline context dictionary.
            abort_signal: Cooperative cancellation signal (unused for now).

        Returns:
            HandlerResult with status=SUCCESS containing the delegation payload.
        """
        # Resolve model and provider from node attrs, falling back to defaults
        model = node.llm_model or self._default_model
        provider = node.llm_provider or self._default_provider

        # Build the delegation payload
        payload = {
            "type": "hermes_delegation",
            "node_id": node.id,
            "node_shape": node.shape,
            "node_label": node.label,
            "node_class": node.node_class,
            "model": model,
            "provider": provider,
            "prompt": prompt,
            "goal": context.get("goal", ""),
            "goal_gate": node.goal_gate or None,
            "reasoning_effort": node.reasoning_effort or None,
            "timeout": node.timeout or None,
            "fidelity": node.fidelity or None,
            "thread_id": node.thread_id or None,
            "context_keys": list(context.keys()),
            "abort_requested": abort_signal.is_set if abort_signal else False,
        }

        # Remove None values for cleanliness
        payload = {k: v for k, v in payload.items() if v is not None}

        payload_json = json.dumps(payload, indent=2)

        # Print structured delegation message for Hermes to bridge later
        print(f"[HERMES DELEGATION] node={node.id}")
        print(payload_json)

        # Return as a successful HandlerResult with the payload
        return HandlerResult(
            status=Outcome.SUCCESS,
            output=payload_json,
            context_updates={
                f"codergen.{node.id}.output": payload_json,
                f"hermes.{node.id}.delegated": True,
            },
            notes=f"Delegated to Hermes: node '{node.id}' with model={model}",
        )