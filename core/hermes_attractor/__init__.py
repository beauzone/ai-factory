"""Hermes-Attractor Bridge — integrates the Attractor pipeline engine with Hermes.

This package provides Hermes-specific implementations of the Attractor
pipeline interfaces, enabling .dot graph specs to orchestrate real work
through Hermes delegate_task and clarify() calls.

Key classes:
    HermesPipelineRunner: Main entry point for running pipelines.
    HermesCodergenBackend: Routes codergen nodes to Hermes delegate_task.
    HermesInterviewer: Routes human-gate nodes to Hermes clarify().
    LinearSink: Publishes pipeline events to Linear issues.

Usage::

    from core.hermes_attractor import HermesPipelineRunner

    runner = HermesPipelineRunner(
        issue_id="BEA-117",
        stylesheet=".frontier { llm_model: claude-opus-4-6; reasoning_effort: high; }",
    )
    result = await runner.run("pipelines/mt-02-credential-broker.dot")
"""

from core.hermes_attractor.backends import HermesCodergenBackend
from core.hermes_attractor.interviewer import HermesInterviewer
from core.hermes_attractor.linear_sink import LinearSink
from core.hermes_attractor.runner import HermesPipelineRunner

__all__ = [
    "HermesPipelineRunner",
    "HermesCodergenBackend",
    "HermesInterviewer",
    "LinearSink",
]