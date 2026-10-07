import pytest

from unfold.app.examples import validate_http_json_examples
from unfold.contracts import ExplanationProposal


def proposal(message: str) -> ExplanationProposal:
    return ExplanationProposal.model_validate(
        {
            "schema_version": "explanation.v1",
            "target_key": "node",
            "overview": "测试报文",
            "resource_requests": [],
            "blocks": [{"type": "markdown", "text": "```http\n" + message + "\n```"}],
        }
    )


def test_utf8_length_not_character_count() -> None:
    body = '{"字":"中"}'
    prefix = "POST /example HTTP/1.1\nContent-Length: "
    validate_http_json_examples(proposal(prefix + str(len(body.encode())) + "\n\n" + body))
    with pytest.raises(ValueError, match="UTF-8 payload"):
        validate_http_json_examples(proposal(prefix + str(len(body)) + "\n\n" + body))


def test_response_length_and_intentional_payload_newline() -> None:
    body = '{"ok":true}\n'
    validate_http_json_examples(
        proposal("HTTP/1.1 200 OK\nContent-Length: " + str(len(body.encode())) + "\n\n" + body)
    )
    with pytest.raises(ValueError):
        validate_http_json_examples(proposal("HTTP/1.1 200 OK\nContent-Length: 99\n\n{}"))


def test_unframed_or_non_json_examples_do_not_claim_validation() -> None:
    validate_http_json_examples(proposal("GET /example HTTP/1.1\nHost: example.com\n"))
    validate_http_json_examples(proposal("HTTP/1.1 200 OK\nContent-Length: 99\n\nnot-json"))


def test_explicit_counterexample_requires_correct_length_diagnosis() -> None:
    value = proposal("HTTP/1.1 200 OK\nContent-Length: 99\n\n{}")
    value.blocks[0].text = "错误示例：\n" + value.blocks[0].text + "\n实际正文为 2 字节。"
    validate_http_json_examples(value)
    value.blocks[0].text = value.blocks[0].text.replace("2 字节", "3 字节")
    with pytest.raises(ValueError):
        validate_http_json_examples(value)
