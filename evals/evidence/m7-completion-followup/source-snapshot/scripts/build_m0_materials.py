"""Author fixed protocol examples and scenario inputs. These are NOT model outputs.

Run only when deliberately changing the dataset version, then review the diff.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from unfold.evaluation import write_json

ROOT = Path(__file__).resolve().parents[1]


def fact(value: str, source: str = "user") -> dict[str, Any]:
    return {
        "value": value,
        "source": source,
        "evidence_keys": ["brief"] if source == "user" else [],
    }


def node(key: str, title: str, state: str = "leaf") -> dict[str, Any]:
    return {
        "key": key,
        "title": title,
        "kind": "concept",
        "concept_ref": None,
        "scope": "有序数组内二分查找的一次中点比较",
        "overview": "比较中点值和目标值，判断保留的区间。",
        "decision": {
            "state": state,
            "reason": "当前目标只要求理解这一步比较，无需进一步拆分。"
            if state == "leaf"
            else "比较和区间更新可分别解释，提供有用的下一层。",
        },
    }


def fixtures() -> list[dict[str, Any]]:
    questionnaire = {
        "schema_version": "questionnaire.v1",
        "rationale": "需了解用户已有基础。",
        "questions": [
            {
                "key": "background",
                "target": "background",
                "prompt": "你了解有序数组吗？",
                "mode": "single",
                "options": [{"key": "yes", "text": "了解"}, {"key": "no", "text": "不了解"}],
                "allow_free_text": True,
                "allow_unknown": True,
            }
        ],
    }
    profile = {
        "schema_version": "profile.v1",
        "topic": fact("二分查找"),
        "primary_purpose": fact("理解一次中点比较"),
        "secondary_purposes": [],
        "background": fact("了解有序数组"),
        "target_abilities": [fact("能判断下一步保留区间")],
        "included_scope": [fact("中点与目标的比较", "assumption")],
        "excluded_scope": [fact("完整算法实现", "assumption")],
        "constraints": [],
    }
    snapshot = {"revision": 1, "profile": profile}
    target = {
        k: v
        for k, v in node("compare", "中点比较", "expandable").items()
        if k not in ("kind", "concept_ref")
    }
    target.update({"profile_revision": 1, "content_revision": 1})
    context = {
        "profile": snapshot,
        "target": target,
        "path": [target],
        "existing_children": [],
        "sibling_scopes": [],
        "reusable_concept_refs": [],
    }
    request = {
        "key": "compare-demo",
        "kind": "interactive",
        "purpose": "观察区间如何改变",
        "specification": "有序数组[1,3,5]中查找3，显示中点比较。",
        "runtime": "web",
        "controls": ["step", "reset"],
        "fallback_text": "中点恰好等于目标，因此本次查找结束。",
    }
    exercise = {
        "key": "q1",
        "mode": "single",
        "stem": "中点值等于目标值时该怎么做？",
        "testing_point": "命中判断",
        "content_refs": ["content-1"],
        "options": [{"key": "found", "text": "返回命中"}, {"key": "left", "text": "继续向左查找"}],
        "correct_keys": ["found"],
        "explanation": "命中时当前查找已经找到目标。",
    }
    results = [
        {
            "operation": "questionnaire",
            "input": {
                "schema_version": "questionnaire-input.v1",
                "domain": "二分查找",
                "user_brief": "只想理解一次中点比较",
                "known_targets": ["goal"],
                "previous_question_keys": [],
                "questions_asked": 0,
                "round": 1,
            },
            "output": questionnaire,
        },
        {
            "operation": "profile",
            "input": {
                "schema_version": "profile-input.v1",
                "domain": "二分查找",
                "user_brief": "我了解有序数组，只想理解一次中点比较",
                "questionnaire": questionnaire,
                "answers": [
                    {
                        "question_key": "background",
                        "selected_keys": ["yes"],
                        "free_text": None,
                        "unknown": False,
                    }
                ],
            },
            "output": profile,
        },
        {
            "operation": "outline",
            "input": {
                "schema_version": "outline-input.v1",
                "profile": snapshot,
                "reusable_concept_refs": [],
            },
            "output": {
                "schema_version": "outline.v1",
                "root": node("root", "一次中点比较", "expandable"),
                "division_basis": "比较结果分类",
                "children": [node("equal", "命中"), node("less", "小于目标")],
                "relations": [],
            },
        },
        {
            "operation": "expansion",
            "input": {"schema_version": "expansion-input.v1", "operation": "expand", **context},
            "output": {
                "schema_version": "expansion.v1",
                "target_key": "compare",
                "decision": {"state": "leaf", "reason": "这一步已足够小"},
                "division_basis": "当前目标无需细分",
                "children": [],
                "relations": [],
            },
        },
        {
            "operation": "reassessment",
            "input": {
                "schema_version": "reassessment-input.v1",
                "feedback": "这个范围是否已经足够？",
                **context,
            },
            "output": {
                "schema_version": "reassessment.v1",
                "target_key": "compare",
                "decision": {"state": "leaf", "reason": "只需理解单次比较"},
                "restructuring_note": None,
            },
        },
        {
            "operation": "explanation",
            "input": {
                "schema_version": "explanation-input.v1",
                "instruction": "举一个比较例子",
                "allowed_resource_kinds": ["interactive"],
                "available_exercise_refs": [],
                **context,
            },
            "output": {
                "schema_version": "explanation.v1",
                "target_key": "compare",
                "overview": "有序数组中点比较",
                "blocks": [
                    {"type": "markdown", "text": "数组 $[1,3,5]$ 的中点值为3。"},
                    {
                        "type": "interactive",
                        "resource_key": "compare-demo",
                        "caption": "命中演示",
                        "fallback_text": request["fallback_text"],
                    },
                ],
                "resource_requests": [request],
            },
        },
        {
            "operation": "exercises",
            "input": {
                "schema_version": "exercises-input.v1",
                "provided_content": [
                    {"revision_ref": "content-1", "text": "中点值等于目标值时返回命中。"}
                ],
                "allowed_modes": ["single", "multiple"],
                "count": 1,
                **context,
            },
            "output": {
                "schema_version": "exercises.v1",
                "target_key": "compare",
                "exercises": [exercise],
            },
        },
        {
            "operation": "resource",
            "input": {
                "schema_version": "resource-input.v1",
                "profile": snapshot,
                "content": {"revision_ref": "content-1", "text": "中点值等于目标时返回命中。"},
                "request": request,
                "supported_kinds": ["interactive"],
                "offered_candidate_refs": ["binary-search-v1"],
            },
            "output": {
                "schema_version": "resource.v1",
                "request_key": "compare-demo",
                "strategy": "reuse",
                "candidate_ref": "binary-search-v1",
                "specification": "使用给定数组和目标配置已有组件",
                "limitation": None,
            },
        },
    ]
    return [{"evidence_kind": "hand_authored_contract_example", **result} for result in results]


def failures(successes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_operation = {sample["operation"]: sample for sample in successes}
    rows = []

    def add(operation: str, name: str, location: list[str | int], value: Any) -> None:
        sample = copy.deepcopy(by_operation[operation])
        target = sample
        for key in location[:-1]:
            target = target[key]
        target[location[-1]] = value
        sample["failure"] = name
        rows.append(sample)

    add(
        "questionnaire",
        "duplicate_question_keys",
        ["output", "questions"],
        by_operation["questionnaire"]["output"]["questions"] * 2,
    )
    add("questionnaire", "repeat_known_target", ["input", "known_targets"], ["background"])
    add("questionnaire", "over_total_question_budget", ["input", "questions_asked"], 5)
    add(
        "profile",
        "assumption_claims_user_evidence",
        ["output", "primary_purpose", "source"],
        "assumption",
    )
    add(
        "profile",
        "invented_evidence",
        ["output", "background", "evidence_keys"],
        ["answer:missing"],
    )
    add(
        "profile",
        "unknown_answer_claimed_as_user",
        ["input", "answers"],
        [{"question_key": "background", "selected_keys": [], "free_text": None, "unknown": True}],
    )
    rows[-1]["output"]["background"]["evidence_keys"] = ["answer:background"]
    add("outline", "leaf_with_children", ["output", "root", "decision", "state"], "leaf")
    add("outline", "forbidden_grandchildren", ["output", "children", 0, "children"], [])
    add(
        "outline",
        "invalid_relation_reference",
        ["output", "relations"],
        [
            {
                "source_key": "root",
                "target_key": "missing",
                "kind": "related_to",
                "explanation": "测试",
            }
        ],
    )
    add(
        "outline",
        "prerequisite_cycle",
        ["output", "relations"],
        [
            {"source_key": a, "target_key": b, "kind": "prerequisite_of", "explanation": "测试"}
            for a, b in [("equal", "less"), ("less", "equal")]
        ],
    )
    add("expansion", "wrong_target", ["output", "target_key"], "other")
    add("expansion", "leaf_cannot_be_expanded", ["input", "target", "decision", "state"], "leaf")
    rows[-1]["input"]["path"] = [copy.deepcopy(rows[-1]["input"]["target"])]
    add("expansion", "empty_expandable_result", ["output", "decision", "state"], "expandable")
    add("reassessment", "wrong_reassessment_target", ["output", "target_key"], "other")
    add(
        "explanation",
        "unknown_resource_reference",
        ["output", "blocks", 1, "resource_key"],
        "missing",
    )
    add("explanation", "explanation_modifies_tree", ["output", "children"], [])
    add("explanation", "unsupported_resource", ["input", "allowed_resource_kinds"], [])
    add(
        "exercises",
        "invalid_answer_reference",
        ["output", "exercises", 0, "correct_keys"],
        ["other"],
    )
    add(
        "exercises",
        "single_choice_has_two_answers",
        ["output", "exercises", 0, "correct_keys"],
        ["found", "left"],
    )
    add(
        "exercises",
        "unprovided_content_reference",
        ["output", "exercises", 0, "content_refs"],
        ["other"],
    )
    add("resource", "invented_resource_candidate", ["output", "candidate_ref"], "invented")
    add("resource", "invented_download_url", ["output", "url"], "https://example.org/invented.png")
    add("resource", "resource_claims_ready", ["output", "status"], "ready")
    for sample in successes:
        add(
            sample["operation"],
            "unsupported_schema_version",
            ["output", "schema_version"],
            "future.v9",
        )
    return rows


def datasets() -> None:
    families = [
        (
            "backend",
            "dev",
            "后端开发",
            [
                (
                    "准备校招，能写简单Python，希望解释HTTP、数据库和并发原理并分析常见故障。",
                    "useful_depth",
                ),
                (
                    "维护已有订单服务，熟悉CRUD，希望解决重复请求、事务一致性和超时故障。",
                    "useful_depth",
                ),
            ],
        ),
        (
            "midpoint",
            "dev",
            "二分查找的一次中点比较",
            [
                (
                    "只想看懂数组[1,3,5]中查找3时中点比较的结果；已理解数组和大小比较，不学完整算法。",
                    "root_leaf",
                ),
                (
                    "辅导同学理解中点值小于目标时为什么保留右半区；只解释这一步，不涉及实现或复杂度。",
                    "root_leaf",
                ),
            ],
        ),
        (
            "cell",
            "dev",
            "高中生物细胞结构",
            [
                (
                    "跟进课堂，希望能区分细胞器的功能和相互关系；目前只知道细胞是基本单位。",
                    "flexible",
                ),
                (
                    "兴趣学习，已学基础细胞结构，想解释蛋白质合成与运输过程；不要求考试范围对齐。",
                    "flexible",
                ),
            ],
        ),
        (
            "triads",
            "dev",
            "音乐理论三和弦",
            [
                ("钢琴伴奏入门，会识谱但不了解和声，希望能构建并听辨基本三和弦。", "flexible"),
                ("编曲使用，会构建三和弦，希望理解转位与声部连接；暂不学习爵士扩展。", "flexible"),
            ],
        ),
        (
            "tang",
            "dev",
            "唐代历史",
            [
                ("兴趣阅读，对年代不熟，希望建立阶段与制度变化的脉络，避免只背人物。", "flexible"),
                ("准备写文化交流短文，已有唐代时序基础，想理解丝路贸易与文化互动。", "flexible"),
            ],
        ),
        (
            "exposure",
            "dev",
            "摄影曝光",
            [
                ("新手拍运动照片，希望理解快门、光圈与ISO如何配合，不考虑后期。", "flexible"),
                ("已会手动曝光，拍夜景时想控制噪声与运动模糊，需要具体取舍。", "flexible"),
            ],
        ),
        (
            "cooking",
            "dev",
            "烹饪传热",
            [
                ("会做家常菜，想理解煎与炖为何有不同结果，不需要复杂物理推导。", "flexible"),
                ("研究烘焙失败，掌握基础温度概念，想理解热传导和水分变化。", "flexible"),
            ],
        ),
        (
            "statistics",
            "dev",
            "统计均值与中位数",
            [
                ("处理工资数据，只需解释极端值如何影响均值与中位数，不学推断统计。", "flexible"),
                ("初学数据分析，想选择合适的中心位置指标，并能说明选择理由。", "flexible"),
            ],
        ),
        (
            "circuits",
            "holdout",
            "直流电路",
            [
                (
                    "维修爱好入门，已理解电压和电流，想解释串并联分压分流，不做危险带电操作。",
                    "flexible",
                ),
                ("准备基础物理课程，能代数运算，希望理解欧姆定律条件并解简单电路。", "flexible"),
            ],
        ),
        (
            "perspective",
            "holdout",
            "绘画透视",
            [
                ("素描入门，想画房间结构，只学一点透视和常见错误。", "flexible"),
                ("有素描经验，希望理解两点透视并绘制建筑街角，不学复杂投影几何。", "flexible"),
            ],
        ),
        (
            "phonemes",
            "holdout",
            "语言学音位",
            [
                (
                    "兴趣入门，想区分语音和音位，能解释最小对立体，不要求跨语言全面覆盖。",
                    "flexible",
                ),
                ("已有音标基础，想分析不同方言的音位差异，避免将口音视作错误。", "flexible"),
            ],
        ),
        (
            "seasons",
            "holdout",
            "天文学季节变化",
            [
                ("给儿童解释季节，已理解地球公转，要区分地轴倾斜和日地距离的影响。", "flexible"),
                ("兴趣拓展，懂基础轨道运动，希望解释南北半球季节相反与纬度差异。", "flexible"),
            ],
        ),
    ]
    scenarios = []
    for family, split, domain, variants in families:
        for index, (brief, behavior) in enumerate(variants, 1):
            scenarios.append(
                {
                    "id": family + "-" + str(index),
                    "family": family,
                    "split": split,
                    "domain": domain,
                    "user_brief": brief,
                    "known_targets": ["purpose", "background", "goal"],
                    "answers_by_target": {
                        "scope": "按原始目标限定，必要前置可补充",
                        "constraints": None,
                    },
                    "expected_behavior": behavior,
                    "max_exploration_depth": 3,
                    "review_targets": [
                        "用途改变组织与例子",
                        "不误称官方或已核验",
                        "分支职责与概述明确",
                        "增量有用且不重复",
                        "停止位置与当前目标相符",
                    ],
                }
            )
    write_json(
        ROOT / "evals/datasets/learning.v1.json", {"version": "learning.v1", "scenarios": scenarios}
    )
    resources = []
    resource_families = [
        ("search_component", "dev", "复用已有二分查找组件，处理命中、未命中和空数组", "reuse"),
        ("http_timeline", "dev", "定制HTTP请求超时时间轴，区分客户端取消与服务端执行", "custom"),
        (
            "function_plot",
            "dev",
            "函数参数演示：初始候选有构建错误，读取错误后修复并重置",
            "repair",
        ),
        (
            "python_database",
            "dev",
            "要求Python读取真实数据库；网页运行环境不支持，应明确退出",
            "unsupported",
        ),
        ("pendulum", "holdout", "单摆参数演示，说明小角度简化并测试极端输入和重置", "custom"),
        (
            "mitosis_image",
            "holdout",
            "细胞分裂显微图片，要求实际来源、许可和下载失败回退",
            "retrieve",
        ),
    ]
    for family, split, task, behavior in resource_families:
        for index in (1, 2):
            resources.append(
                {
                    "id": family + "-" + str(index),
                    "family": family,
                    "split": split,
                    "task": task + ("；键盘操作" if index == 1 else "；减少动态效果"),
                    "expected_behavior": behavior,
                    "checks": ["实际产物可用", "文字回退", "能力与来源可追溯", "越界被拒绝"],
                }
            )
    write_json(
        ROOT / "evals/datasets/resources.v1.json", {"version": "resources.v1", "tasks": resources}
    )


if __name__ == "__main__":
    successes = fixtures()
    write_json(ROOT / "evals/contracts/success.v1.json", successes)
    write_json(ROOT / "evals/contracts/failure.v1.json", failures(successes))
    datasets()
