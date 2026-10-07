import json
import time
from pathlib import Path

import pytest

from unfold.app.browser import BrowserProcess, validate_web
from unfold.app.components import SearchParameters, search_component
from unfold.app.web_checks import check_web
from unfold.provider import GenerationError

ROOT = Path(__file__).resolve().parents[1]


def test_managed_browser_isolated_interaction_reset_and_key_stripping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-secret-must-not-enter-renderer")
    html = (
        "<button onclick=\"document.querySelector('output').textContent='1'\">step</button>"
        "<output>0</output>"
    )
    with BrowserProcess(html) as browser:
        assert browser.metadata["origin"] == "null" and browser.metadata["browser_sandbox"]
        assert not browser.metadata["model_key_in_driver"]
        isolated = browser.command("probe")
        assert isolated["storage"] == "unavailable" and isolated["network"] == "blocked"
        assert isolated["parentIsSelf"] and isolated["nodeProcess"] == "undefined"
        browser.command("selector_click", selector="button")
        assert browser.command("read_value", selector="output")["text"] == "1"
        browser.command("reset")
        assert browser.command("read_value", selector="output")["text"] == "0"
        assert browser.screenshot().startswith(b"\x89PNG")
        metadata = browser.metadata
    assert browser.process.poll() is not None
    (ROOT / ".data/qa/m5-isolation.json").write_text(
        json.dumps(
            {
                "metadata": metadata,
                "probe": isolated,
                "interaction_reset_passed": True,
                "closed": True,
                "strict_memory_quota": False,
                "claims": "finite browser probes, not universal containment",
            },
            indent=2,
        )
        + "\n"
    )


def test_watchdog_terminates_busy_renderer_without_blocking_application() -> None:
    started = time.monotonic()
    # Deliberately bypass only the static preflight for this fault injection.
    # Actual execution still uses own Chromium sandbox, deny proxy, routing and CSP.
    with pytest.raises(GenerationError) as error:
        BrowserProcess("<script>while(true){}</script>", timeout=3, validate=False)
    assert error.value.code in {"timeout", "domain_validation"}
    assert time.monotonic() - started < 10
    with BrowserProcess("<p>new-process-after-timeout</p>") as browser:
        assert "new-process-after-timeout" in browser.command("inspect")["body"]


@pytest.mark.parametrize(
    "html",
    [
        "<script>fetch('https://example.com')</script>",
        "<script src='anything.js'></script>",
        "<iframe src='about:blank'></iframe>",
        "<input type=file>",
        "<meta http-equiv=refresh content=0>",
        "<script>new Worker('worker.js')</script>",
        "<script>while(true){}</script>",
    ],
)
def test_web_capabilities_reject_network_files_imports_and_obvious_loops(html: str) -> None:
    with pytest.raises(GenerationError) as error:
        validate_web(html)
    assert error.value.code == "capability_limit"


def test_real_bounded_component_checks_hit_miss_empty_keyboard_reset() -> None:
    result = check_web(
        search_component(SearchParameters(values=[1, 3, 5], target=3)), "search_component-1"
    )
    assert result["passed"] and len(result["checks"]) == 6
    assert result["runtime"]["browser_sandbox"]
    (ROOT / ".data/qa/m5-component-check.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    )
