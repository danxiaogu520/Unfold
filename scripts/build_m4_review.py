"""Create a transparent review manuscript; computed checks are not human judgments."""

import json
from pathlib import Path

from unfold.contracts import Exercise, grade

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    batches = [
        ("HTTP/1.1", ROOT / "evals/runs/m4-live-v1/run_4e9126d2dad147ec897cd6a5770dfe1e.json"),
        ("一次中点比较与语言整数规则", ROOT / "evals/runs/m4-reference-correction-v1"),
    ]
    correction = json.loads((batches[1][1] / "report.json").read_text())
    run_id = next(row["run_id"] for row in correction["tasks"] if row["operation"] == "exercises")
    batches[1] = (batches[1][0], batches[1][1] / f"{run_id}.json")
    lines = [
        "# M4 题目审阅稿",
        "",
        "日期：2026-10-07。共六题；下面展示答案是为了审阅，普通练习接口不会在首次作答前返回答案或解析。",
        "",
        "HTTP 三题来自真实 DeepSeek 输出；中点三题基于真实输出进行本地规范修订，"
        "没有新增供应商推理。代码代理做了逐题复核，独立人审尚未执行。",
        "",
        "本次确定性检查仅验证引用、选项和集合判分，不能得出教学效果或知识准确率。",
        "",
        "已处理的问题：旧中点讲解混写 C/Java，旧多选题遗漏非负下标条件；"
        "正文旧版已标记，旧多选题已停用，新版明确前提并保留历史。",
        "",
        "## 复核依据",
        "",
        "- [RFC 9112 消息体边界](https://www.rfc-editor.org/rfc/rfc9112.html#section-6.3)",
        "- [RFC 9110 字段行与 Set-Cookie 例外](https://www.rfc-editor.org/rfc/rfc9110.html#section-5.2)",
        "- [Python 内建整数](https://docs.python.org/3/library/stdtypes.html#numeric-types-int-float-complex)",
        "- [Java 整数加法](https://docs.oracle.com/en/java/javase/26/docs/specs/jls/jls-15.html#jls-15.18.2)",
        "- [C 委员会表达式越界规则说明](https://open-std.org/jtc1/sc22/wg14/www/docs/n2396.htm)",
        "",
    ]
    rows = []
    for label, path in batches:
        trace = json.loads(path.read_text())
        lines += ["## " + label, ""]
        for index, raw in enumerate(trace["proposal"]["exercises"], 1):
            exercise = Exercise.model_validate(raw)
            assert grade(exercise, exercise.correct_keys)
            wrong = next(
                option.key for option in exercise.options if option.key not in exercise.correct_keys
            )
            assert not grade(exercise, [wrong])
            lines += [
                f"### {index}. {exercise.stem}",
                "",
                "题型：" + exercise.mode + "；考点：" + exercise.testing_point,
                "",
            ]
            lines += [f"- `{option.key}`：{option.text}" for option in exercise.options]
            lines += [
                "",
                "审阅用参考答案：" + "、".join(exercise.correct_keys),
                "",
                exercise.explanation,
                "",
                "依赖内容：" + "、".join(exercise.content_refs),
                "",
            ]
            rows.append(
                {
                    "key": exercise.key,
                    "content_refs": exercise.content_refs,
                    "correct_selection_passed": True,
                    "wrong_selection_rejected": True,
                }
            )
    lines += [
        "## 尚待独立人审",
        "",
        "请逐题检查：前提是否明确、答案是否唯一或集合完整、是否超出提供的正文、解析是否真正支持答案。独立人审和学习效果仍未知，不把代码代理检查替代为真人评分。",
        "",
    ]
    destination = ROOT / "docs/reports/m4-question-review-2026-10-07.md"
    destination.write_text("\n".join(lines))
    (ROOT / ".data/qa/m4-question-checks.json").write_text(
        json.dumps(
            {
                "questions": rows,
                "deterministic_cases": 12,
                "agent_review": "targeted source comparison",
                "independent_human_review": None,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    print(json.dumps({"questions": len(rows), "deterministic_cases": 12, "human_review": None}))


if __name__ == "__main__":
    main()
