"""Narrow deterministic checks for displayed, complete HTTP/1.x JSON examples."""

import json
import re

from unfold.contracts import Contract, ExplanationProposal, MarkdownBlock


def validate_http_json_examples(proposal: Contract) -> None:
    if not isinstance(proposal, ExplanationProposal):
        return
    errors = []
    for block_index, block in enumerate(proposal.blocks):
        if not isinstance(block, MarkdownBlock):
            continue
        for code_index, match in enumerate(
            re.finditer(r"```[^\n]*\n(.*?)```", block.text, flags=re.S)
        ):
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
                before = block.text[max(0, match.start() - 100) : match.start()]
                after = block.text[match.end() : match.end() + 300]
                if re.search(
                    r"错误示例|故意错误|反例|invalid example|counterexample", before, re.I
                ) and re.search(
                    rf"(?:实际|正确|应为|expected)[^\n]{{0,40}}\b{expected}\s*(?:字节|bytes)",
                    after,
                    re.I,
                ):
                    continue  # Explicit counterexample with correct byte diagnosis.
                errors.append(
                    {
                        "block_index": block_index,
                        "code_index": code_index,
                        "declared_bytes": lengths,
                        "expected_bytes": expected,
                        "payload": body,
                    }
                )
    if errors:
        raise ValueError(
            "displayed HTTP JSON Content-Length must match UTF-8 payload "
            "without Markdown fence newline: " + json.dumps(errors, ensure_ascii=False)
        )
