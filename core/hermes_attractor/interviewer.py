"""Hermes-backed Interviewer for the Attractor pipeline.

Routes human-gate questions to Hermes clarify() via the CLI. When the
Hermes CLI is not available, falls back to console input (stdin).

If a LinearSync is configured, posts the question as a comment on the
associated Linear issue for visibility.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys
from typing import Any

from attractor_pipeline.handlers.human import Answer, Question

log = logging.getLogger(__name__)


class HermesInterviewer:
    """Interviewer that routes human-gate questions to Hermes clarify.

    Tries `hermes clarify` CLI first. Falls back to stdin when the CLI
    is unavailable or in non-interactive mode (e.g., piped stdin).
    """

    def __init__(
        self,
        *,
        issue_id: str | None = None,
        linear_sync: Any | None = None,
    ) -> None:
        """Initialize the interviewer.

        Args:
            issue_id: Optional Linear issue ID for posting questions.
            linear_sync: Optional LinearSync instance for posting
                questions as Linear comments.
        """
        self._issue_id = issue_id
        self._linear_sync = linear_sync

    async def ask(self, question: Question) -> Answer:
        """Ask a human via Hermes clarify or console fallback.

        Posts the question to Linear for visibility, then tries the
        Hermes CLI. Falls back to stdin if CLI is unavailable.

        Args:
            question: The Question object from the pipeline engine.

        Returns:
            An Answer with the user's response.
        """
        # Post to Linear if configured
        if self._linear_sync and self._issue_id:
            options_text = ""
            if question.options:
                options_text = f"\nOptions: {', '.join(str(o) for o in question.options)}"
            self._linear_sync.post_event(
                self._issue_id,
                f"🤔 **Human Gate** (`{question.stage or 'unknown'}`): "
                f"{question.text}{options_text}",
            )

        # Try Hermes clarify CLI
        try:
            cmd = ["hermes", "clarify", "--question", question.text]
            if question.options:
                cmd.extend(["--choices", ",".join(str(o) for o in question.options)])

            result = await asyncio.to_thread(
                subprocess.run, cmd, capture_output=True, text=True, timeout=3600
            )
            if result.returncode == 0:
                value = result.stdout.strip()
                selected = value if (question.options and value in question.options) else None
                # Post answer to Linear
                if self._linear_sync and self._issue_id:
                    self._linear_sync.post_event(
                        self._issue_id,
                        f"👤 **Human responded**: {value}",
                    )
                return Answer(value=value, selected_option=selected, text=value)

        except (FileNotFoundError, subprocess.TimeoutExpired):
            log.warning("[HERMES INTERVIEWER] CLI not available, falling back to console")

        # Console fallback
        return await self._ask_console(question)

    async def _ask_console(self, question: Question) -> Answer:
        """Fall back to console input for human-gate questions.

        In non-interactive mode (piped stdin), auto-approves by
        selecting the first option or the default.
        """
        # Print question and options
        print(f"\n{'=' * 60}")
        print(f"[HERMES HUMAN GATE: {question.stage or 'unknown'}]")
        print(question.text)

        if question.options:
            print("\nOptions:")
            for i, opt in enumerate(question.options, 1):
                print(f"  {i}. {opt}")

        # Auto-approve in non-interactive mode
        if not sys.stdin.isatty():
            default_value = question.default or ""
            if question.options and not default_value:
                default_value = str(question.options[0])
            print(f"\n  [Non-interactive mode: auto-selected '{default_value}']")
            selected = default_value if (question.options and default_value in question.options) else None
            return Answer(value=default_value, selected_option=selected, text=default_value)

        # Interactive: prompt for input
        prompt_text = "\n> "
        value = await asyncio.to_thread(input, prompt_text)
        value = value.strip()

        if not value and question.default:
            value = question.default

        # Handle numbered option selection
        if question.options and value.isdigit():
            idx = int(value) - 1
            if 0 <= idx < len(question.options):
                value = str(question.options[idx])

        selected = value if (question.options and value in question.options) else None
        return Answer(value=value, selected_option=selected, text=value)