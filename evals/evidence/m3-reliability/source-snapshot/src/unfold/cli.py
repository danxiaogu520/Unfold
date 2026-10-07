"""Local application commands and separately budgeted model experiments."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from unfold.evaluation import Experiment, read_json, write_json
from unfold.materials import check_materials, export, verify_evidence
from unfold.provider import GenerationError, Settings, load_env_file


def main() -> None:
    parser = argparse.ArgumentParser(description="Unfold local learning application and evaluation")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    commands = parser.add_subparsers(dest="command", required=True)
    schema = commands.add_parser("export-schemas")
    schema.add_argument("--check", action="store_true")
    commands.add_parser("check")
    commands.add_parser("preflight")
    commands.add_parser("verify-evidence")
    for name in ("migrate", "serve", "worker"):
        app_command = commands.add_parser(name)
        app_command.add_argument("--app-env", type=Path, default=Path(".env.app.local"))
        if name == "worker":
            app_command.add_argument("--model-env", type=Path)
            app_command.add_argument("--once", action="store_true")
    baseline = commands.add_parser("baseline")
    baseline.add_argument("--env-file", type=Path)
    baseline.add_argument("--scenario", action="append", default=[])
    baseline.add_argument("--repetitions", type=int, default=3)
    baseline.add_argument("--probes", action="store_true")
    baseline.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        if args.command in ("migrate", "serve", "worker"):
            from unfold.app.config import AppSettings, load_app_env
            from unfold.app.db import Database
            from unfold.app.migrate import migrate

            if args.app_env.exists():
                load_app_env(args.app_env)
            settings_app = AppSettings.from_env()
            database = Database(settings_app)
            if args.command == "migrate":
                try:
                    migrate(database)
                    print(json.dumps({"migration": "head"}))
                finally:
                    database.close()
            elif args.command == "serve":
                import uvicorn

                from unfold.app.api import create_app

                uvicorn.run(
                    create_app(settings_app, database),
                    host=settings_app.host,
                    port=settings_app.port,
                    log_level="info",
                )
            else:
                from unfold.app.generation import DeepSeekGenerator, FixtureGenerator, Generator
                from unfold.app.service import RunService
                from unfold.app.worker import Worker

                service = RunService(database, settings_app)
                generator: Generator
                if settings_app.model_mode == "deepseek":
                    if args.model_env:
                        load_env_file(args.model_env)
                    generator = DeepSeekGenerator(database, service, Settings.from_env(), root)
                else:
                    generator = FixtureGenerator(database, root)
                try:
                    worker = Worker(service, generator)
                    if args.once:
                        print(json.dumps({"executed": worker.tick()}))
                    else:
                        worker.run()
                finally:
                    database.close()
        elif args.command == "export-schemas":
            print(json.dumps({"schemas": export(root, check=args.check)}))
        elif args.command == "check":
            print(json.dumps(check_materials(root), ensure_ascii=False, indent=2))
        elif args.command == "verify-evidence":
            print(json.dumps(verify_evidence(root), ensure_ascii=False, indent=2))
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
                if any(not task["success"] for task in experiment.tasks):
                    parser.exit(1, "baseline contains failed tasks; see preserved report\n")
            finally:
                experiment.close()
    except (GenerationError, ValueError) as exc:
        parser.exit(2, f"{getattr(exc, 'code', 'validation')}: {exc}\n")
    except Exception as exc:
        if args.command in ("migrate", "serve", "worker"):
            from sqlalchemy.exc import SQLAlchemyError

            if isinstance(exc, SQLAlchemyError):
                parser.exit(
                    2,
                    "configuration: database unavailable or migration failed; "
                    "check docs/development.md\n",
                )
        raise


if __name__ == "__main__":
    main()
