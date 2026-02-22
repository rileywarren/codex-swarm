from __future__ import annotations

from textwrap import shorten

from .models import ReturnFormat, WorkerExecutionResult


class ResultCompressor:
    def __init__(self, max_summary_tokens: int = 500, max_diff_lines: int = 200):
        self.max_summary_tokens = max_summary_tokens
        self.max_diff_lines = max_diff_lines

    def _summary_block(self, result: WorkerExecutionResult) -> str:
        lines = [
            f"Worker: {result.worker_id}",
            f"Status: {result.status.value}",
            f"Result: {result.result.status.value}",
            f"Summary: {result.result.summary}",
        ]

        if result.result.files_modified:
            lines.append("Files modified: " + ", ".join(result.result.files_modified))
        if result.result.files_created:
            lines.append("Files created: " + ", ".join(result.result.files_created))
        if result.result.files_deleted:
            lines.append("Files deleted: " + ", ".join(result.result.files_deleted))
        if result.result.key_decisions:
            lines.append("Key decisions: " + " | ".join(result.result.key_decisions))
        if result.result.warnings:
            lines.append("Warnings: " + " | ".join(result.result.warnings))

        lines.append(f"Tests: {result.result.tests_status}")
        lines.append(f"Confidence: {result.result.confidence:.2f}")

        text = "\n".join(lines)
        max_chars = self.max_summary_tokens * 4
        return shorten(text, width=max_chars, placeholder=" ...")

    def _truncate_diff(self, diff_text: str) -> str:
        lines = diff_text.splitlines()
        if len(lines) <= self.max_diff_lines:
            return diff_text
        head = "\n".join(lines[: self.max_diff_lines])
        return head + f"\n... [truncated {len(lines) - self.max_diff_lines} lines]"

    def compress(self, result: WorkerExecutionResult, fmt: ReturnFormat) -> str:
        summary = self._summary_block(result)
        if fmt == ReturnFormat.SUMMARY:
            return summary

        if fmt == ReturnFormat.DIFF:
            diff_block = self._truncate_diff(result.diff_text)
            return f"{summary}\n\nDiff:\n```diff\n{diff_block}\n```"

        full_output = result.raw_stdout
        return (
            f"{summary}\n\nDiff:\n```diff\n{result.diff_text}\n```\n\n"
            f"Raw stdout:\n```text\n{full_output}\n```"
        )
