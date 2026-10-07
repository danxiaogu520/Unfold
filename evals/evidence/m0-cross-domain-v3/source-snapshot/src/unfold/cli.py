"""M0 tooling CLI. Live calls are only made by the explicit `baseline` command."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from unfold.evaluation import Experiment, read_json, write_json
from unfold.materials import check_materials, export
from unfold.provider import GenerationError, Settings, load_env_file


def main() -> None:
    parser = argparse.ArgumentParser(description="Unfold M0 contracts and model experiments")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    commands = parser.add_subparsers(dest="command", required=True)
    schema = commands.add_parser("export-schemas")
    schema.add_argument("--check", action="store_true")
    commands.add_parser("check")
    commands.add_parser("preflight")
    baseline = commands.add_parser("baseline")
    baseline.add_argument("--env-file", type=Path)
    baseline.add_argument("--scenario", action="append", default=[])
    baseline.add_argument("--repetitions", type=int, default=3)
    baseline.add_argument("--probes", action="store_true")
    baseline.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        if args.command == "export-schemas":
            print(json.dumps({"schemas": export(root, check=args.check)}))
        elif args.command == "check":
            print(json.dumps(check_materials(root), ensure_ascii=False, indent=2))
        elif args.command == "preflight":
            settings = Settings.from_env()
            print(
                json.dumps(
                    {
                        "configuration": "ready",
                        "model": settings.model,
                        "currency": settings.currency,
                        "max_cost": str(settings.max_cost),
                    }
                )
            )
        elif args.command == "baseline":
            if args.env_file:
                load_env_file(args.env_file)
            settings = Settings.from_env()
            if not 1 <= args.repetitions <= 3:
                raise GenerationError("configuration", "repetitions must be 1..3")
            scenarios = read_json(root / "evals/datasets/learning.v1.json")["scenarios"]
            selected_ids = set(args.scenario or ["backend-1", "midpoint-1"])
            selected = [row for row in scenarios if row["id"] in selected_ids]
            if {row["id"] for row in selected} != selected_ids:
                raise GenerationError("configuration", "unknown scenario ID")
            if any(row["split"] != "dev" for row in selected):
                raise GenerationError(
                    "configuration", "M0 baseline cannot tune on holdout scenarios"
                )
            experiment = Experiment(root, settings, args.output)
            write_json(
                experiment.directory / "selection.json",
                {
                    "scenario_ids": sorted(selected_ids),
                    "repetitions": args.repetitions,
                    "probes": args.probes,
                    "selection_frozen_before_calls": True,
                },
            )
            try:
                for row in selected:
                    for repetition in range(1, args.repetitions + 1):
                        experiment.learning_chain(row, repetition)
                if args.probes:
                    for sample in read_json(root / "evals/contracts/success.v1.json"):
                        experiment.probe(sample["operation"], sample)
                    experiment.compatibility_checks()
                print(
                    json.dumps(
                        {"directory": str(experiment.directory), "summary": experiment.report()},
                        ensure_ascii=False,
                        indent=2,
                    )
                )
            finally:
                experiment.close()
    except (GenerationError, ValueError) as exc:
        parser.exit(2, f"{getattr(exc, 'code', 'validation')}: {exc}\n")


if __name__ == "__main__":
    main()
