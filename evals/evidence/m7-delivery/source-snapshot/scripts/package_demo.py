"""Private local source package with locks and evidence; never bundle live data or keys."""

import argparse
import hashlib
import json
import shutil
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {
    ".git",
    ".data",
    ".tools",
    ".venv",
    "node_modules",
    "__pycache__",
    "dist",
    "runs",
    "test-results",
    "playwright-report",
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    archive = output.with_suffix(".tar.gz")
    if output.exists() or archive.exists():
        raise SystemExit("Package output must be new")
    output.mkdir(parents=True)
    paths = [
        p
        for name in ("src", "scripts", "web", "contracts", "evals", "docs", ".github")
        for p in (ROOT / name).rglob("*")
        if p.is_file() and not any(part in EXCLUDED for part in p.relative_to(ROOT).parts)
    ]
    paths += [
        ROOT / name
        for name in (
            "README.md",
            "PRODUCT.md",
            "DESIGN.md",
            "PLAN.md",
            "pyproject.toml",
            "uv.lock",
            ".python-version",
            ".gitignore",
            ".env.example",
            ".env.app.example",
        )
    ]
    fingerprints = {}
    for source in sorted(set(paths)):
        if source.is_symlink():
            raise SystemExit("Source symlink cannot enter package")
        if source.name.startswith(".env") and source.name not in (
            ".env.example",
            ".env.app.example",
        ):
            raise SystemExit("Private environment file cannot enter package")
        name = str(source.relative_to(ROOT))
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        fingerprints[name] = hashlib.sha256(source.read_bytes()).hexdigest()
    fingerprint = hashlib.sha256(
        json.dumps(fingerprints, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    (output / "build-manifest.json").write_text(
        json.dumps(
            {
                "version": "m7-private-source-package.v1",
                "source_fingerprint": fingerprint,
                "git_commit": None,
                "remote_ci_run": None,
                "live_database_or_model_credentials_included": False,
                "files": fingerprints,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    with tarfile.open(archive, "w:gz") as bundle:
        bundle.add(output, arcname=output.name)
    print(
        json.dumps(
            {
                "source_fingerprint": fingerprint,
                "files": len(fingerprints),
                "archive": str(archive),
                "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            }
        )
    )


if __name__ == "__main__":
    main()
