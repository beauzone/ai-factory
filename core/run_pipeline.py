#!/usr/bin/env python3
"""AI Software Factory — Pipeline Runner CLI.

Runs an Attractor DOT pipeline spec with Hermes backends.

Usage:
    # Dry run (no LLM calls, auto-approve human gates)
    python run_pipeline.py templates/feature-impl.dot --dry-run

    # Run with Linear issue tracking
    python run_pipeline.py templates/feature-impl.dot --issue BEA-117

    # Run with custom context variables
    python run_pipeline.py templates/feature-impl.dot --context goal="Implement credential broker" --issue BEA-117

    # Run with specific workdir for delegate tasks
    python run_pipeline.py templates/feature-impl.dot --workdir /path/to/repo --issue BEA-117
"""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

# Add samueljklee-attractor to path
ATTRACTOR_PATH = Path(__file__).parent.parent / "samueljklee-attractor" / "src"
if ATTRACTOR_PATH.exists():
    sys.path.insert(0, str(ATTRACTOR_PATH))

# Add our core to path
CORE_PATH = Path(__file__).parent
if CORE_PATH not in sys.path:
    sys.path.insert(0, str(CORE_PATH))

from hermes_bridge import (
    HermesCodergenBackend,
    HermesInterviewer,
    LinearEventSink,
    LinearSync,
    build_registry,
)
from attractor_pipeline.parser import parse_dot
from attractor_pipeline.validation import validate
from attractor_pipeline.engine.runner import run_pipeline, PipelineStatus
from attractor_pipeline.handlers import AutoApproveInterviewer


def parse_context_args(args: list[str]) -> dict:
    """Parse --context key=value pairs into a dict."""
    ctx = {}
    for arg in args:
        if "=" in arg:
            key, value = arg.split("=", 1)
            ctx[key.strip()] = value.strip()
    return ctx


async def main():
    parser = argparse.ArgumentParser(description="Run an Attractor DOT pipeline with Hermes backends")
    parser.add_argument("dot_file", help="Path to the DOT pipeline spec")
    parser.add_argument("--issue", help="Linear issue ID (e.g., BEA-117) for event tracking")
    parser.add_argument("--workdir", help="Working directory for delegate tasks")
    parser.add_argument("--context", nargs="*", default=[], help="Context variables as key=value pairs")
    parser.add_argument("--dry-run", action="store_true", help="Dry run: no LLM calls, auto-approve gates")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    log = logging.getLogger("pipeline_runner")

    # Parse the DOT file
    dot_path = Path(args.dot_file)
    if not dot_path.exists():
        print(f"Error: DOT file not found: {dot_path}", file=sys.stderr)
        sys.exit(1)

    log.info(f"Loading pipeline: {dot_path}")
    graph = parse_dot(dot_path.read_text())

    # Validate
    issues = validate(graph)
    if issues:
        log.warning(f"Validation issues: {len(issues)}")
        for issue in issues:
            log.warning(f"  {issue}")

    log.info(f"Pipeline: {graph.name or 'unnamed'}")
    log.info(f"Goal: {graph.goal}")
    log.info(f"Nodes: {', '.join(graph.nodes.keys())}")
    log.info(f"Edges: {len(graph.edges)}")

    # Build backends
    if args.dry_run:
        log.info("DRY RUN: using AutoApproveInterviewer, no LLM calls")
        backend = None
        interviewer = AutoApproveInterviewer()
    else:
        backend = HermesCodergenBackend(workdir=args.workdir)
        interviewer = HermesInterviewer(
            issue_id=args.issue,
            linear_sync=LinearSync() if args.issue else None,
        )

    registry = build_registry(
        codergen_backend=backend,
        interviewer=interviewer,
    )

    # Build event sink
    on_event = None
    if args.issue:
        sink = LinearEventSink(issue_id=args.issue)
        on_event = sink.on_event

    # Parse context
    context = parse_context_args(args.context)
    if context:
        log.info(f"Context: {context}")

    # Run
    log.info("Starting pipeline execution...")
    result = await run_pipeline(
        graph,
        registry,
        context=context,
        on_event=on_event,
    )

    # Report results
    print(f"\n{'='*60}")
    print(f"Pipeline: {graph.name or 'unnamed'}")
    print(f"Status: {result.status.value}")
    print(f"Duration: {result.duration_seconds:.1f}s")
    print(f"Completed nodes: {len(result.completed_nodes)}")

    if result.error:
        print(f"Error: {result.error}")

    if result.final_outcome:
        print(f"Final outcome: {result.final_outcome.status.value}")
        if result.final_outcome.output:
            print(f"Output: {result.final_outcome.output[:500]}")

    if result.context:
        # Print codergen outputs
        for key, value in sorted(result.context.items()):
            if key.startswith("codergen.") and key.endswith(".output"):
                stage = key.replace("codergen.", "").replace(".output", "")
                print(f"\n--- {stage} output ---")
                print(str(value)[:2000])

    print(f"{'='*60}")

    sys.exit(0 if result.status == PipelineStatus.COMPLETED else 1)


if __name__ == "__main__":
    asyncio.run(main())