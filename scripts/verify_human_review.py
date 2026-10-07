"""Read-only verification of the user's explicit six-question math review."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    for name in ("math-review-v1", "math-review-human-answers-v1", "math-review-human-verdict-v1"):
        folder = ROOT / "evals/evidence" / name
        manifest = json.loads((folder / "manifest.json").read_text())
        for path, expected in manifest["files"].items():
            file = (folder / path).resolve()
            assert file.is_relative_to(folder) and file.is_file()
            assert hashlib.sha256(file.read_bytes()).hexdigest() == expected, path
    answer_file = ROOT / "evals/evidence/math-review-human-answers-v1/receipt.json"
    answers = json.loads(answer_file.read_text())
    verdict = json.loads(
        (ROOT / "evals/evidence/math-review-human-verdict-v1/verdict.json").read_text()
    )
    assert verdict["answers_receipt_sha256"] == hashlib.sha256(answer_file.read_bytes()).hexdigest()
    assert answers["matched"] == answers["total"] == len(verdict["questions"]) == 6
    assert verdict["user_message"] == "六题没有问题"
    assert verdict["reviewer_role"] == "human_user"
    assert verdict["m4_01_sample_human_inspection"] == "passed"
    assert all(q["human_quality_verdict"] == "no_issues_reported" for q in verdict["questions"])
    assert verdict["general_quality_accuracy"] is None
    assert verdict["learning_effectiveness"] is None
    assert verdict["m6_proxy_human_calibration"] is None
    print(
        json.dumps(
            {
                "human_questions": 6,
                "sample_review": "passed",
                "new_model_calls": 0,
                "general_accuracy": None,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
