"""Build a trusted, offline viewer of frozen evidence; never execute model source."""

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Use a new demo output")
    recovery = json.loads(
        (ROOT / "evals/evidence/m3-reliability/m3-process-response_saved.json").read_text()
    )
    race = json.loads((ROOT / "evals/evidence/m3-reliability/m3-cancel-race.json").read_text())
    batch = ROOT / "evals/evidence/m5-agent/m5-public-comparison-v2"
    comparison = json.loads((batch / "report.json").read_text())
    trial = next(
        t
        for t in comparison["trials"]
        if t["case_id"] == "function_plot-1" and t["mode"] == "agent"
    )
    trace = json.loads((batch / (trial["run_id"] + ".json")).read_text())
    agent_recovery = json.loads(
        (ROOT / "evals/evidence/m5-agent/m5-public-recovery-v2/report.json").read_text()
    )
    context = json.loads(
        (ROOT / "evals/evidence/m6-context/heldout-v1/derived-metrics.json").read_text()
    )
    data = {
        "jobs": {
            "title": "持久任务：恢复与提交资格",
            "label": "历史真实 PostgreSQL / SIGKILL · 模型替身",
            "source": "evals/evidence/m3-reliability/m3-process-response_saved.json",
            "run_id": recovery["trace"]["snapshot"]["run_id"],
            "steps": [
                {"title": "第一次领取并保存候选", "detail": "attempt=1；候选已保存，尚未发布。"},
                {"title": "进程 SIGKILL", "detail": "实际子进程被终止；租约到期进入 retry_wait。"},
                {
                    "title": "第二次领取",
                    "detail": "attempt=2；stored_response_replay，network_call=false。",
                },
                {
                    "title": "一个正式结果",
                    "detail": json.dumps(
                        {
                            "saved_intakes": recovery["saved_intakes"],
                            "request_replay_reused": recovery["request_replay_reused"],
                            "cancel_races": race,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                },
            ],
            "limit": "外部调用可能重复执行和收费；20 次竞争与四个窗口不构成任意并发证明。",
        },
        "agent": {
            "title": "资源 Agent：按实际反馈修复",
            "label": "历史真实 DeepSeek 决策 + 独立 Chromium 检查",
            "source": "evals/evidence/m5-agent/m5-public-comparison-v2/"
            + trial["run_id"]
            + ".json",
            "run_id": trial["run_id"],
            "steps": [
                {
                    "title": str(step["number"]) + " · " + str(step["decision"]["tool"]),
                    "detail": json.dumps(
                        {
                            "origin": step["origin"],
                            "reason": step["decision"]["reason"],
                            "passed": step["observation"].get("passed"),
                            "page_errors": step["observation"].get("page_errors"),
                            "candidate_id": step["observation"].get("candidate_id"),
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                }
                for step in trace["agent_steps"]
            ],
            "limit": (
                "初始检查是所有流程共享的固定 bootstrap；后续四次才是模型决策。"
                "技术通过不代替教学核验。"
            ),
        },
        "context": {
            "title": "上下文：保留更合适的基线",
            "label": "8 个公开保留场景 · 72 单元 · 正式发布 0",
            "source": "evals/evidence/m6-context/heldout-v1/derived-metrics.json",
            "run_id": "m6-public-context-comparison.v1",
            "steps": [
                {"title": name, "detail": json.dumps(values, ensure_ascii=False, indent=2)}
                for name, values in context["strategies"].items()
            ],
            "limit": (
                "输入量、契约与词覆盖代理有记录；独立人审与事实准确率未知，继续以当前路径为默认值。"
            ),
        },
    }
    data["agent"]["steps"].extend(
        [
            {
                "title": "另一资源 run：工具保存后 SIGKILL",
                "detail": json.dumps(
                    {
                        "source": "evals/evidence/m5-agent/m5-public-recovery-v2/report.json",
                        "run_id": agent_recovery["run_id"],
                        "child_sigkill": agent_recovery["child_sigkill"],
                        "before_calls": agent_recovery["before_calls"],
                        "completed_steps": agent_recovery["completed_steps_not_replayed"],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            },
            {
                "title": "另一个 attempt 恢复：已完成工具不重放",
                "detail": json.dumps(
                    {
                        "source": "evals/evidence/m5-agent/"
                        "m5-public-recovery-v2/after-recovery.json",
                        "run_id": agent_recovery["run_id"],
                        "restoration_integrity_passed": agent_recovery[
                            "restoration_integrity_passed"
                        ],
                        "after_calls": agent_recovery["after_calls"],
                        "final_status": agent_recovery["final_status"],
                        "unknown_usage_calls": agent_recovery["unknown_usage_calls"],
                        "cost_cny": agent_recovery["conservative_cost_cny"],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            },
        ]
    )
    encoded = json.dumps(data, ensure_ascii=False).replace(chr(60), chr(92) + "u003c")
    html = (
        (ROOT / "web/runtime/evidence-viewer.html")
        .read_text()
        .replace("__EVIDENCE_JSON__", encoded)
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(html)
    print(json.dumps({"output": str(args.output), "recorded_model_calls_reexecuted": 0}))


if __name__ == "__main__":
    main()
