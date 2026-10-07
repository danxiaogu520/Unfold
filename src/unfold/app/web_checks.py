"""Independent executable checks. Model/tool arguments cannot replace these rules."""

from __future__ import annotations

import hashlib
import time
from typing import Any

from unfold.app.browser import BrowserProcess
from unfold.provider import GenerationError

SPECIFICATIONS: dict[str, dict[str, Any]] = {
    "search_component-1": {
        "type": "binary_search",
        "interface": {
            "step": "#step",
            "reset": "#reset",
            "values": "#values",
            "target": "#target",
            "apply": "#apply",
            "result": "#result",
        },
        "checks": (
            "hit, miss, empty array; ArrowRight performs one step, #step also performs one step; "
            "after repeated ArrowRight #result is exactly 找到，下标 N or 未找到; "
            "#reset restores #result to 等待开始, including empty input. Arrays <=32."
        ),
    },
    "function_plot-1": {
        "type": "linear_function",
        "interface": {
            "slope": "#slope",
            "intercept": "#intercept",
            "x": "#x",
            "result": "#value",
            "reset": "#reset",
        },
        "checks": "f(x)=k*x+b; values -3,0,1.5,3; reset restores k=1,b=0,x=2; visible plot",
    },
    "python_database-1": {
        "type": "unsupported",
        "checks": "Python and SQLite are outside this web runtime",
    },
    "generic": {
        "type": "generic",
        "checks": "loading, no page errors, repeatable reset; teaching correctness unknown",
    },
}


def specification(case_id: str) -> dict[str, Any]:
    if case_id not in SPECIFICATIONS:
        raise GenerationError(
            "capability_limit", "no independently defined web check for this case"
        )
    return SPECIFICATIONS[case_id]


def check_web(html: str, case_id: str) -> dict[str, Any]:
    spec = specification(case_id)
    if spec["type"] == "unsupported":
        raise GenerationError("capability_limit", "requested Python/database runtime unsupported")
    started = time.monotonic()
    results: list[dict[str, Any]] = []
    with BrowserProcess(html) as browser:
        initial = browser.command("inspect")
        if spec["type"] == "binary_search":
            for values, target, expected in (
                ("2,7,11,14,18,27,33,54,61,95", "27", "找到，下标 5"),
                ("1,3,5", "4", "未找到"),
                ("", "2", "未找到"),
            ):
                browser.command("set_value", selector="#values", value=values)
                browser.command("set_value", selector="#target", value=target)
                browser.command("selector_click", selector="#apply")
                for _ in range(8):
                    browser.command("key", key="ArrowRight")
                actual = browser.command("read_value", selector="#result")["text"]
                results.append(
                    {
                        "values": values,
                        "target": target,
                        "expected": expected,
                        "actual": actual,
                        "passed": actual == expected,
                    }
                )
                browser.command("selector_click", selector="#reset")
                reset = browser.command("read_value", selector="#result")["text"]
                results.append({"reset": True, "actual": reset, "passed": reset == "等待开始"})
        elif spec["type"] == "linear_function":
            plot_hashes = set()
            for k, b, x in ((-3, 2, -2), (0, -1, 3), (1.5, -2, 2), (3, 1, 1)):
                for selector, value in (("#slope", k), ("#intercept", b), ("#x", x)):
                    browser.command("set_value", selector=selector, value=str(value))
                text = browser.command("read_value", selector="#value")["text"]
                try:
                    actual = float(text)
                except ValueError:
                    actual = None
                results.append(
                    {
                        "parameters": [k, b, x],
                        "expected": k * x + b,
                        "actual": actual,
                        "passed": actual is not None and abs(actual - (k * x + b)) < 1e-8,
                    }
                )
                plot = browser.command("plot_fingerprint")
                if plot["visible"]:
                    plot_hashes.add(plot["sha256"])
            browser.command("selector_click", selector="#reset")
            text = browser.command("read_value", selector="#value")["text"]
            results.append(
                {"reset": True, "actual": text, "passed": text.strip() in {"2", "2.0", "2.00"}}
            )
            results.append(
                {
                    "visible_plot_changes": True,
                    "distinct_plot_images": len(plot_hashes),
                    "passed": len(plot_hashes) >= 2,
                }
            )
        else:
            browser.command("reset")
            reset = browser.command("inspect")
            results.append({"loaded": True, "passed": bool(initial["body"].strip())})
            results.append({"reset": True, "passed": initial["body"] == reset["body"]})
        final = browser.command("inspect")
        errors = [item for item in final["logs"] if item.startswith("page-error:")]
        passed = all(row["passed"] for row in results) and not errors and not final["blocked"]
        return {
            "version": "independent-web-check.v1",
            "case_id": case_id,
            "passed": passed,
            "source_sha256": hashlib.sha256(html.encode()).hexdigest(),
            "checks": results,
            "page_errors": errors,
            "blocked_requests": final["blocked"],
            "runtime": browser.metadata,
            "seconds": round(time.monotonic() - started, 3),
            "knowledge_correctness": "limited behavioural checks only",
        }
