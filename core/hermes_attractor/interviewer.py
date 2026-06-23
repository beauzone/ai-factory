"""Hermes-backed Interviewer for human-in-the-loop pipeline nodes.

Implements the Interviewer protocol from attractor_pipeline.handlers.human
using stdin/stdout for interactive human input. This is the CLI/terminal
bridge that will later be replaced by Hermes' delegation system.

Usage::

    interviewer = HermesInterviewer()
    handler = HumanHandler(interviewer=interviewer)
    registry.register("wait.human", handler)
"""

from __future__ import annotations

import asyncio

from attractor_pipeline.handlers.human import Answer, Question


class HermesInterviewer:
    """Interviewer that prompts on stdout and reads from stdin.

    Satisfies the Interviewer protocol: ``ask(Question) -> Answer``.

    For now, this uses simple blocking stdin input. When Hermes bridge
    is wired up, this will delegate to Hermes' task system instead.
    """

    async def ask(self, question: Question) -> Answer:
        """Ask the human a question and wait for a response via stdin.

        Prints the question text and options to stdout, then reads
        an answer from stdin (blocking). Runs input() in a thread
        to avoid blocking the event loop.

        Args:
            question: Structured Question descriptor with text, options,
                type hint, default, and metadata.

        Returns:
            Answer with the human's response value, selected option
            match (if applicable), and display text.
        """
        # Build the prompt
        prompt_parts = [f"\n{'='*60}"]
        prompt_parts.append(f"[HERMES HUMAN GATE: {question.stage}]")
        prompt_parts.append(question.text)

        if question.options:
            prompt_parts.append("Options:")
            for i, option in enumerate(question.options, 1):
                prompt_parts.append(f"  {i}. {option}")
            prompt_parts.append("Enter your choice (number or text):")
        else:
            prompt_parts.append("Enter your response:")

        if question.default:
            prompt_parts.append(f"(Default: {question.default})")

        prompt_parts.append(f"{'='*60}")
        prompt_parts.append("> ")

        full_prompt = "\n".join(prompt_parts)

        # Read answer — auto-approve in non-interactive mode
        import sys

        if not sys.stdin.isatty():
            # Non-interactive: auto-approve with first option or default
            if question.options:
                value = question.options[0]
                selected_option = value
            else:
                value = question.default or "approved"
                selected_option = None
            print(f"  [Non-interactive mode: auto-selected '{value}']")
        else:
            value = await asyncio.to_thread(input, full_prompt)
            selected_option = None

        # Handle numbered option selection (only for interactive mode)
        selected_option: str | None = None
        if question.options:
            try:
                idx = int(value) - 1
                if 0 <= idx < len(question.options):
                    value = question.options[idx]
                    selected_option = value
            except (ValueError, IndexError):
                pass

            # If the raw value matches an option directly, mark it
            if value in question.options:
                selected_option = value

        # Fall back to default if empty
        if not value.strip() and question.default:
            value = question.default
            if question.options and value in question.options:
                selected_option = value

        return Answer(
            value=value,
            selected_option=selected_option,
            text=value,
        )

    async def ask_question(self, question: Question) -> Answer:
        """Compatibility alias for ask()."""
        return await self.ask(question)