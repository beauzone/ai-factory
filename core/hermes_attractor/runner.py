"""Hermes Pipeline Runner -- main entry point for running Attractor pipelines.

Ties together the Hermes-specific backends, interviewer, and event sink
with the Attractor pipeline engine. Loads a .dot file, sets up the handler
registry with our custom components, and runs the pipeline.

Usage::

    runner = HermesPipelineRunner()
    result = await runner.run("pipelines/hello_hermes.dot")
    print(f"Status: {result.status}")
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from attractor_pipeline.engine.runner import (
    HandlerRegistry,
    PipelineResult,
    PipelineStatus,
    run_pipeline,
)
from attractor_pipeline.handlers.codergen import CodergenHandler
from attractor_pipeline.handlers.human import HumanHandler
from attractor_pipeline.parser import parse_dot
from attractor_pipeline.validation import validate_or_raise

from core.hermes_attractor.backends import HermesCodergenBackend
from core.hermes_attractor.interviewer import HermesInterviewer
from core.hermes_attractor.linear_sink import LinearSink


class HermesPipelineRunner:
    """Main entry point for running Attractor pipelines with Hermes integration.

    Loads a .dot pipeline file, configures the handler registry with
    Hermes-specific backends and interviewer, runs the pipeline, and
    reports results through the Linear event sink.

    Example::

        runner = HermesPipelineRunner()
        result = await runner.run("pipelines/hello_hermes.dot")
        if result.status == PipelineStatus.COMPLETED:
            print("Pipeline succeeded!")
    """

    def __init__(
        self,
        *,
        default_model: str = "claude-sonnet-4-5",
        default_provider: str | None = None,
        linear_issue_id: str | None = None,
    ) -> None:
        """Initialize the runner with Hermes components.

        Args:
            default_model: Default LLM model for codergen nodes.
            default_provider: Default LLM provider for codergen nodes.
            linear_issue_id: Optional Linear issue ID for event tracking.
        """
        self._backend = HermesCodergenBackend(
            default_model=default_model,
            default_provider=default_provider,
        )
        self._interviewer = HermesInterviewer()
        self._linear_sink = LinearSink(issue_id=linear_issue_id)

    def _build_registry(self) -> HandlerRegistry:
        """Build a HandlerRegistry with Hermes-specific handlers.

        Registers the standard handlers (start, exit, conditional, tool,
        parallel, fan_in, manager) plus our custom codergen and
        human handlers wired to the Hermes backends.

        Returns:
            A configured HandlerRegistry ready for pipeline execution.
        """
        from attractor_pipeline.handlers import register_default_handlers

        registry = HandlerRegistry()
        register_default_handlers(
            registry,
            codergen_backend=self._backend,
            interviewer=self._interviewer,
        )
        return registry

    async def run(
        self,
        dot_path: str,
        *,
        context: dict[str, Any] | None = None,
        logs_dir: str | None = None,
    ) -> PipelineResult:
        """Load and execute a .dot pipeline file.

        Args:
            dot_path: Path to the DOT pipeline file.
            context: Optional initial context dictionary.
            logs_dir: Optional directory for pipeline logs and checkpoints.

        Returns:
            PipelineResult with status, context, completed nodes, etc.

        Raises:
            FileNotFoundError: If the .dot file doesn't exist.
            ValueError: If the .dot file fails validation.
        """
        path = Path(dot_path)
        if not path.exists():
            raise FileNotFoundError(f"Pipeline file not found: {dot_path}")

        # Parse
        source = path.read_text(encoding="utf-8")
        graph = parse_dot(source)

        # Validate
        validate_or_raise(graph)

        # Emit start event
        self._linear_sink.on_event("pipeline.started", {
            "pipeline": graph.name,
            "goal": graph.goal or "",
            "nodes": len(graph.nodes),
            "edges": len(graph.edges),
            "dot_file": str(path),
        })

        # Build registry
        registry = self._build_registry()

        # Set up logs directory
        logs_root = Path(logs_dir) if logs_dir else None
        if logs_root:
            logs_root.mkdir(parents=True, exist_ok=True)

        # Execute
        start_time = time.monotonic()

        result = await run_pipeline(
            graph,
            registry,
            context=context or {},
            logs_root=logs_root,
        )

        duration = time.monotonic() - start_time

        # Emit completion event
        self._linear_sink.on_event("pipeline.completed", {
            "status": result.status.value,
            "duration_seconds": round(duration, 2),
            "completed_nodes": result.completed_nodes,
            "error": result.error or "",
        })

        return result

    async def run_dot_string(
        self,
        dot_source: str,
        *,
        context: dict[str, Any] | None = None,
        logs_dir: str | None = None,
    ) -> PipelineResult:
        """Execute a DOT pipeline from a string.

        Like run() but takes the DOT source string directly instead
        of a file path. Useful for testing and inline pipelines.

        Args:
            dot_source: The DOT pipeline source string.
            context: Optional initial context dictionary.
            logs_dir: Optional directory for pipeline logs.

        Returns:
            PipelineResult with status, context, completed nodes, etc.

        Raises:
            ValueError: If the DOT source fails validation.
        """
        graph = parse_dot(dot_source)
        validate_or_raise(graph)

        self._linear_sink.on_event("pipeline.started", {
            "pipeline": graph.name,
            "goal": graph.goal or "",
            "nodes": len(graph.nodes),
            "edges": len(graph.edges),
            "source": "inline_dot_string",
        })

        registry = self._build_registry()
        logs_root = Path(logs_dir) if logs_dir else None
        if logs_root:
            logs_root.mkdir(parents=True, exist_ok=True)

        start_time = time.monotonic()
        result = await run_pipeline(
            graph,
            registry,
            context=context or {},
            logs_root=logs_root,
        )
        duration = time.monotonic() - start_time

        self._linear_sink.on_event("pipeline.completed", {
            "status": result.status.value,
            "duration_seconds": round(duration, 2),
            "completed_nodes": result.completed_nodes,
            "error": result.error or "",
        })

        return result