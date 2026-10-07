"""Review workbook and separate answer key from retained real math outputs."""

import hashlib
import json
import shutil
from fractions import Fraction
from pathlib import Path

from unfold.contracts import Exercise, grade, validate_pair

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "evals/runs/math-review-v1"
FROZEN = ROOT / "evals/evidence/math-review-v1"
WORKBOOK = ROOT / "docs/reports/high-school-math-review-2026-10-07.md"
ANSWERS = ROOT / "docs/reports/high-school-math-answers-2026-10-07.md"


def main() -> None:
    if FROZEN.exists() or WORKBOOK.exists() or ANSWERS.exists():
        raise SystemExit("Do not overwrite a review version")
    report = json.loads((SOURCE / "report.json").read_text())
    assert len(report["tasks"]) == 4 and all(t["status"] == "succeeded" for t in report["tasks"])
    workbook = [
        "# 高中数学审阅工作单",
        "",
        "这轮审阅改为二次函数和简单概率，共六题。先读每组原讲解，自己选答案，"
        "最后打开[答案与解析](high-school-math-answers-2026-10-07.md)对照。",
        "",
        "每题只需标记：通过 / 有问题 / 待查。有问题或待查时加一句原因；"
        "检查题意、条件、单选唯一/多选完整、解析与答案、是否超出讲解或偏离用途。",
        "",
        "两份讲解和六题来自真实 DeepSeek 生成；范围/大纲为固定公开评测脚手架。"
        "没有假设你个人的学习经历，不宣称官方高考题或课程标准对齐。"
        "代理核对与独立人审分开，目前人审仍未执行。",
        "",
    ]
    answers = [
        "# 高中数学答案与解析",
        "",
        "先完成工作单再对照。以下是模型已保存答案，仍允许指出错误或歧义。",
        "",
    ]
    checks = []
    labels = []
    for case, title, prefix in (
        ("quadratic", "二次函数", "函数"),
        ("probability", "简单概率", "概率"),
    ):
        task = next(
            t for t in report["tasks"] if t["case"] == case and t["operation"] == "exercises"
        )
        trace = json.loads((SOURCE / (task["run_id"] + ".json")).read_text())
        validate_pair("exercises", trace["input"], trace["proposal"])
        workbook += ["## " + title, "", "### 本组提供的原讲解", ""]
        for content in trace["input"]["provided_content"]:
            text = "\n".join(
                "##" + line if line.startswith("#") else line
                for line in content["text"].splitlines()
            )
            workbook += [text, "", "正文修订：" + content["revision_ref"], ""]
        for index, raw in enumerate(trace["proposal"]["exercises"], 1):
            question = Exercise.model_validate(raw)
            label = prefix + "-" + str(index)
            labels.append(label)
            letters = {option.key: chr(65 + n) for n, option in enumerate(question.options)}
            workbook += [
                "### " + label + " " + question.stem,
                "",
                "题型：" + ("单选" if question.mode == "single" else "多选"),
                "",
            ]
            workbook += [letters[o.key] + ". " + o.text for o in question.options]
            workbook += ["", "你的答案：____", "审阅结论：通过 / 有问题 / 待查；原因：____", ""]
            answers += [
                "## " + label,
                "",
                question.stem,
                "",
                "已保存参考答案：" + "、".join(letters[k] for k in question.correct_keys),
                "",
                question.explanation,
                "",
                "考查点：" + question.testing_point,
                "",
            ]
            assert grade(question, question.correct_keys)
            wrong = next(o.key for o in question.options if o.key not in question.correct_keys)
            assert not grade(question, [wrong])
            checks.append(
                {
                    "label": label,
                    "run_id": task["run_id"],
                    "question_key": question.key,
                    "content_refs": question.content_refs,
                    "letters_to_keys": {letter: key for key, letter in letters.items()},
                    "saved_answer_grade_replays": True,
                }
            )
    workbook += ["## 直接回复这六行", "", chr(96) * 3 + "text"]
    workbook += [label + "：通过 / 有问题 / 待查；原因" for label in labels]
    workbook += [
        chr(96) * 3,
        "",
        "你也可以只说哪些题有问题，以及其余哪些题通过。"
        "这是一份题目抽检，不代表整体教学效果已验证。",
        "",
    ]
    numeric = {
        "f_2": 2**2 - 4 * 2 + 3,
        "f_1": 1**2 - 4 * 1 + 3,
        "f_3": 3**2 - 4 * 3 + 3,
        "dice_even": str(Fraction(3, 6)),
        "coin_at_least_one_head": str(Fraction(3, 4)),
    }
    assert numeric == {
        "f_2": -1,
        "f_1": 0,
        "f_3": 0,
        "dice_even": "1/2",
        "coin_at_least_one_head": "3/4",
    }
    WORKBOOK.write_text("\n".join(workbook))
    ANSWERS.write_text("\n".join(answers))
    shutil.copytree(SOURCE, FROZEN)
    shutil.copyfile(WORKBOOK, FROZEN / WORKBOOK.name)
    shutil.copyfile(ANSWERS, FROZEN / ANSWERS.name)
    shutil.copyfile(Path(__file__).resolve(), FROZEN / "build_math_review.py")
    summary = {
        "version": "math-review-materials.v1",
        "questions": checks,
        "numeric_reference_checks": numeric,
        "agent_review": "targeted elementary calculation and source comparison",
        "independent_human_review": None,
        "real_calls": report["real_calls"],
        "conservative_cost_cny": report["conservative_cost_cny"],
        "project_total_cny": "2.937820",
        "remaining_authorized_cny": "7.062180",
    }
    (FROZEN / "review-checks.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    )
    files = {
        str(p.relative_to(FROZEN)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in FROZEN.rglob("*")
        if p.is_file()
    }
    (FROZEN / "manifest.json").write_text(
        json.dumps(
            {"version": "math-review-manifest.v1", "files": files}, ensure_ascii=False, indent=2
        )
        + "\n"
    )
    print(
        json.dumps(
            {
                "questions": len(checks),
                "files": len(files),
                "real_calls": report["real_calls"],
                "cost_cny": report["conservative_cost_cny"],
            }
        )
    )


if __name__ == "__main__":
    main()
