"""Narrow deterministic checks for displayed, complete HTTP/1.x JSON examples."""

import json
import re

from unfold.contracts import Contract, ExplanationProposal, MarkdownBlock


def validate_http_json_examples(proposal: Contract) -> None:
    if not isinstance(proposal, ExplanationProposal):
        return
    for block in proposal.blocks:
        if not isinstance(block, MarkdownBlock):
            continue
        for match in re.finditer(r"```[^\n]*\n(.*?)```", block.text, flags=re.S):
            code = match[1].replace("\r\n", "\n")
            if not re.match(r"(?:[A-Z]+ \S+ HTTP/1\.[01]|HTTP/1\.[01] \d{3})", code):
                continue
            headers, separator, body = code.partition("\n\n")
            if not separator:
                continue
            # The final line ending belongs to Markdown's closing fence. A JSON
            # payload's deliberate additional newlines remain part of its bytes.
            if body.endswith("\n"):
                body = body[:-1]
            try:
                json.loads(body)
            except (ValueError, RecursionError):
                continue  # Incomplete/other body formats are outside this checker.
            lengths = re.findall(r"(?im)^Content-Length:[ \t]*(\d+)[ \t]*$", headers)
            expected = len(body.encode("utf-8"))
            if lengths and any(int(value) != expected for value in lengths):
                raise ValueError(
                    "displayed HTTP JSON Content-Length must match UTF-8 payload "
                    f"without Markdown fence newline; expected {expected} bytes"
                )
