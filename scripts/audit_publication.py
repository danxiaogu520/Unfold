"""Audit Git-selected public files without printing credentials or private file contents."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_PARTS = {
    ".git",
    ".data",
    ".tools",
    ".venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "test-results",
    "playwright-report",
    "dist",
    "runs",
}
ENV_EXAMPLES = {".env.example", ".env.app.example"}
PRIVATE_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".dump", ".pem", ".key"}
CREDENTIAL = re.compile(
    rb"sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9_]{30,}|"
    rb"github_pat_[A-Za-z0-9_]{30,}|(?:AKIA|ASIA)[A-Z0-9]{16}|"
    rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
)
LOCAL_HOME = re.compile(
    rb"/(?:Users|home)/[A-Za-z0-9][A-Za-z0-9._-]*(?:/|\b)|"
    rb"[A-Za-z]:\\Users\\[A-Za-z0-9][A-Za-z0-9._-]*\\"
)


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=ROOT)


def local_secrets() -> list[bytes]:
    secrets = []
    for name in (".env.local", ".env.app.local"):
        path = ROOT / name
        if not path.is_file():
            continue
        for line in path.read_text().splitlines():
            key, separator, value = line.partition("=")
            value = value.strip().strip("\"'")
            if (
                separator
                and any(word in key.upper() for word in ("KEY", "TOKEN", "SECRET", "PASSWORD"))
                and len(value) >= 12
            ):
                secrets.append(value.encode())
    return secrets


def audit(staged: bool) -> dict[str, object]:
    index = {}
    for row in git("ls-files", "--stage", "-z").split(b"\0"):
        if row:
            metadata, name = row.decode().split("\t", 1)
            mode, digest, stage = metadata.split()
            index[name] = (mode, digest, stage)
    selected = set(index)
    if not staged:
        selected.update(
            p.decode()
            for p in git("ls-files", "--others", "--exclude-standard", "-z").split(b"\0")
            if p
        )
    secrets = local_secrets()
    findings = []
    total_bytes = 0
    object_format = git("rev-parse", "--show-object-format").decode().strip()
    for name in sorted(selected):
        path = ROOT / name
        reasons = []
        if (
            any(part in PRIVATE_PARTS for part in path.relative_to(ROOT).parts)
            or (path.name.startswith(".env") and path.name not in ENV_EXAMPLES)
            or path.suffix in PRIVATE_SUFFIXES
            or path.name in {"id_rsa", "id_ed25519", ".DS_Store"}
        ):
            reasons.append("private_file_or_generated_directory")
        if path.is_symlink() or not path.resolve().is_relative_to(ROOT) or not path.is_file():
            findings.append({"file": name, "reasons": [*reasons, "missing_or_unsafe_file"]})
            continue
        data = path.read_bytes()
        total_bytes += len(data)
        if CREDENTIAL.search(data) or any(secret in data for secret in secrets):
            reasons.append("credential_pattern")
        if LOCAL_HOME.search(data):
            reasons.append("personal_absolute_path")
        if len(data) > 50 * 1024 * 1024:
            reasons.append("oversized_git_file")
        if staged:
            mode, expected, stage = index[name]
            actual = hashlib.new(object_format, f"blob {len(data)}\0".encode() + data).hexdigest()
            if mode not in {"100644", "100755"} or stage != "0":
                reasons.append("unsafe_or_unmerged_index_entry")
            if actual != expected:
                reasons.append("index_differs_from_audited_worktree")
        if reasons:
            findings.append({"file": name, "reasons": reasons})
    # A manifest must never reference a log or source file omitted by .gitignore.
    for name in sorted(selected):
        path = ROOT / name
        if path.name != "manifest.json" or not name.startswith("evals/evidence/"):
            continue
        manifest = json.loads(path.read_text())
        for relative in manifest.get("files", {}):
            reference = path.parent / relative
            if not reference.resolve().is_relative_to(ROOT):
                findings.append({"file": name, "reasons": ["unsafe_evidence_reference"]})
            elif reference.relative_to(ROOT).as_posix() not in selected:
                findings.append({"file": name, "reasons": ["evidence_file_missing_from_git"]})
    return {
        "version": "publication-audit.v1",
        "scope": "index_with_matching_worktree" if staged else "git_selected_worktree",
        "files": len(selected),
        "bytes": total_bytes,
        "findings": findings,
        "real_model_calls": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staged", action="store_true")
    args = parser.parse_args()
    report = audit(args.staged)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["files"] or report["findings"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
