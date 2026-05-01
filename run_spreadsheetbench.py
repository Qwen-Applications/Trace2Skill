#!/usr/bin/env python3
"""
SpreadsheetBench runner for the public Trace2Skill repository.

Supports both the CLI-only baseline and the skill-preloaded spreadsheet agent.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from tqdm import tqdm

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from react_agent import ApiChatClient, OpenAIClient
from spreadsheet_agent import CLIOnlyAgent, CLISkillPreloadedAgent, SpreadsheetBenchRunner


ALLOWED_SKILL_DIR_NAMES = {"xlsx", "xlsx-122B", "xlsx-35B"}
AVAILABLE_AGENTS = {
    "cli_only": CLIOnlyAgent,
    "cli_skill_preloaded": CLISkillPreloadedAgent,
}


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a SpreadsheetBench agent.")
    parser.add_argument(
        "--data_path",
        type=str,
        required=True,
        help="Path to SpreadsheetBench data directory or JSONL file",
    )
    parser.add_argument(
        "--output_dir",
        type=str,
        default="outputs/spreadsheetbench",
        help="Directory to save output spreadsheets",
    )
    parser.add_argument(
        "--working_dir",
        type=str,
        default=None,
        help="Working directory for agent execution",
    )
    parser.add_argument(
        "--agent",
        type=str,
        default="cli_skill_preloaded",
        choices=list(AVAILABLE_AGENTS.keys()),
        help="Agent to use",
    )
    parser.add_argument(
        "--skills_dir",
        type=str,
        default=str(Path(__file__).resolve().parent / "spreadsheet_agent" / "skills"),
        help="Path to spreadsheet skills root directory for cli_skill_preloaded",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="gpt-4o",
        help="Model to use",
    )
    parser.add_argument(
        "--llm_client",
        type=str,
        default="openai",
        choices=["openai", "api_chat"],
        help="LLM client backend to use",
    )
    parser.add_argument(
        "--api_chat_config",
        type=str,
        default="config/llm_api.json",
        help="Path to ApiChat config JSON when --llm_client=api_chat",
    )
    parser.add_argument(
        "--max_turns",
        type=int,
        default=None,
        help="Maximum turns per task",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.0,
        help="Temperature for generation",
    )
    parser.add_argument(
        "--generation_config",
        type=str,
        default=None,
        help="Generation config as JSON string or path to JSON file",
    )
    parser.add_argument(
        "--num_random_seeds",
        type=int,
        default=1,
        help="Number of runs with randomly generated seeds",
    )
    parser.add_argument(
        "--seeds",
        type=str,
        default=None,
        help="Comma-separated explicit seeds",
    )
    parser.add_argument(
        "--start_idx",
        type=int,
        default=0,
        help="Start index for benchmark instances",
    )
    parser.add_argument(
        "--end_idx",
        type=int,
        default=None,
        help="End index for benchmark instances (exclusive)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print verbose output",
    )
    parser.add_argument(
        "--results_file",
        type=str,
        default=None,
        help="Path to save results JSON",
    )
    parser.add_argument(
        "--log_dir",
        type=str,
        default=None,
        help="Directory to save chat history logs",
    )
    parser.add_argument(
        "--log_format",
        type=str,
        default="markdown",
        choices=["markdown", "jsonl"],
        help="Format for chat history logs",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Number of parallel workers",
    )
    parser.add_argument(
        "--instance_ids",
        type=str,
        default=None,
        help="Comma-separated list of instance IDs to run",
    )
    parser.add_argument(
        "--missing_only",
        action="store_true",
        help="Only run instances that do not have complete output files",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="Repeat the benchmark with different random seeds",
    )
    parser.add_argument(
        "--shuffle_seed",
        type=int,
        default=None,
        help="Shuffle instances with this fixed seed",
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=None,
        help="After shuffling, take only the first N instances",
    )
    return parser


def validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if args.repeat > 1 and args.num_random_seeds > 1:
        parser.error("--repeat and --num_random_seeds > 1 are mutually exclusive")
    if args.num_random_seeds < 1:
        parser.error("--num_random_seeds must be >= 1")
    if args.agent == "cli_skill_preloaded":
        resolved_skills_dir = _resolve_skills_dir(args.skills_dir)
        if not resolved_skills_dir.is_dir():
            parser.error(f"skills directory not found: {resolved_skills_dir}")
        _validate_allowed_skills(resolved_skills_dir)


def _resolve_skills_dir(skills_dir: str) -> Path:
    path = Path(skills_dir).resolve()
    if (path / "SKILL.md").is_file():
        path = path.parent
    return path


def _validate_allowed_skills(skills_dir: Path) -> None:
    discovered = {
        child.name
        for child in skills_dir.iterdir()
        if child.is_dir() and (child / "SKILL.md").is_file()
    }
    unexpected = sorted(discovered - ALLOWED_SKILL_DIR_NAMES)
    if unexpected:
        raise ValueError(
            "Only spreadsheet skills "
            f"{sorted(ALLOWED_SKILL_DIR_NAMES)} are allowed, found extra skills: {unexpected}"
        )
    missing = sorted(ALLOWED_SKILL_DIR_NAMES - discovered)
    if missing:
        raise ValueError(f"Missing required spreadsheet skills: {missing}")


def _parse_generation_config(generation_config: str | None) -> dict:
    if not generation_config:
        return {}
    if os.path.isfile(generation_config):
        with open(generation_config, "r", encoding="utf-8") as fp:
            parsed = json.load(fp)
    else:
        parsed = json.loads(generation_config)
    if not isinstance(parsed, dict):
        raise ValueError("--generation_config must be a JSON object or a path to a JSON object file")
    return parsed


def _build_generation_config(args) -> dict:
    generation_config = _parse_generation_config(args.generation_config)
    run_seed = getattr(args, "run_seed", None)
    if run_seed is not None:
        generation_config["seed"] = run_seed
    return generation_config


def _build_client(args):
    generation_config = _build_generation_config(args)
    run_seed = generation_config.get("seed")
    use_cache = run_seed is None
    if args.llm_client == "api_chat":
        return ApiChatClient(
            model=args.model,
            config_path=args.api_chat_config,
            generation_config=generation_config or None,
            use_cache=use_cache,
        )
    return OpenAIClient(
        model=args.model,
        api_key=os.getenv("OPENAI_API_KEY"),
        base_url=os.getenv("OPENAI_BASE_URL"),
        generation_config=generation_config or None,
        use_cache=use_cache,
    )


def _build_openai_client(args):
    return _build_client(args)


def _parse_seed_csv(seed_csv: str) -> list[int]:
    seeds: list[int] = []
    for token in seed_csv.split(","):
        token = token.strip()
        if not token:
            continue
        seeds.append(int(token))
    if not seeds:
        raise ValueError("No seeds provided in --seeds")
    return seeds


def _resolve_run_seeds(args) -> list[int | None]:
    if args.seeds:
        return _parse_seed_csv(args.seeds)
    if args.num_random_seeds > 1:
        rng = random.SystemRandom()
        return rng.sample(range(1, 2_147_483_647), args.num_random_seeds)
    return [None]


def _prepare_instances(instances: list, shuffle_seed: int | None, sample: int | None) -> list:
    if shuffle_seed is not None:
        rng = random.Random(shuffle_seed)
        instances = list(instances)
        rng.shuffle(instances)
    if sample is not None:
        instances = instances[:sample]
    return instances


def _results_file_for_seed(base_results_file: str | None, seed: int) -> str | None:
    if base_results_file is None:
        return None
    root, ext = os.path.splitext(base_results_file)
    if not ext:
        ext = ".json"
    return f"{root}_seed_{seed}{ext}"


def create_agent(args, worker_id: int = 0, scripts_dir: str | None = None):
    """Create an agent instance for a worker."""
    client = _build_openai_client(args)

    agent_class = AVAILABLE_AGENTS[args.agent]
    agent_kwargs = {
        "client": client,
        "temperature": args.temperature,
        "verbose": args.verbose,
    }
    if args.max_turns is not None:
        agent_kwargs["max_turns"] = args.max_turns
    if args.log_dir:
        agent_kwargs["log_dir"] = args.log_dir
        agent_kwargs["log_format"] = args.log_format

    # For cli_script_making agent, each worker needs its own script_library
    if args.agent == "cli_script_making" and scripts_dir is not None:
        agent_kwargs["scripts_dir"] = scripts_dir

    # For skill agents, pass custom skills directory if specified
    if args.agent in ("cli_skill", "cli_skill_preloaded") and getattr(args, "skills_dir", None):
        skills_path = os.path.abspath(args.skills_dir)
        # If the path points to a specific skill folder (contains SKILL.md),
        # use its parent as the skills root directory
        if os.path.isfile(os.path.join(skills_path, "SKILL.md")):
            skills_path = os.path.dirname(skills_path)
        agent_kwargs["skills_dir"] = skills_path

    return agent_class(**agent_kwargs)


def instance_has_outputs(instance, output_dir: str, data_path: str) -> bool:
    instance_id = str(instance.id)
    spreadsheet_path = str(instance.spreadsheet_path)
    output_candidates = [
        os.path.join(output_dir, spreadsheet_path),
        os.path.join(output_dir, instance_id),
    ]
    output_instance_dir = next((p for p in output_candidates if os.path.isdir(p)), None)
    if output_instance_dir is None:
        return False

    data_dir = data_path if os.path.isdir(data_path) else os.path.dirname(data_path)
    spreadsheet_dir_candidates = [
        os.path.join(data_dir, "spreadsheet", spreadsheet_path),
        os.path.join(data_dir, spreadsheet_path),
        os.path.join(data_dir, "spreadsheet", instance_id),
        os.path.join(data_dir, instance_id),
    ]
    spreadsheet_dir = next((p for p in spreadsheet_dir_candidates if os.path.isdir(p)), None)
    if spreadsheet_dir is None:
        return False

    files = os.listdir(spreadsheet_dir)
    input_files = [f for f in files if f.endswith("_input.xlsx")]
    if not input_files:
        input_files = [f for f in files if f.endswith("_init.xlsx")]
    if not input_files:
        if "initial.xlsx" in files:
            input_files = ["initial.xlsx"]
        elif "input.xlsx" in files:
            input_files = ["input.xlsx"]
    if not input_files:
        return False

    output_files = os.listdir(output_instance_dir)
    for input_file in input_files:
        if "_input.xlsx" in input_file:
            expected_output = input_file.replace("_input.xlsx", "_output.xlsx")
        elif "_init.xlsx" in input_file:
            expected_output = input_file.replace("_init.xlsx", "_output.xlsx")
        else:
            expected_output = f"{os.path.splitext(input_file)[0]}_output.xlsx"
        if expected_output not in output_files:
            return False
    return True


def filter_instances(instances, args, data_path: str):
    if args.instance_ids:
        requested_ids = {item.strip() for item in args.instance_ids.split(",")}
        instances = [inst for inst in instances if str(inst.id) in requested_ids]
        found_ids = {str(inst.id) for inst in instances}
        not_found = requested_ids - found_ids
        if not_found:
            print(f"Warning: Instance IDs not found in dataset: {', '.join(sorted(not_found))}")
    if args.missing_only:
        original_count = len(instances)
        instances = [
            inst for inst in instances
            if not instance_has_outputs(inst, args.output_dir, data_path)
        ]
        skipped = original_count - len(instances)
        print(f"Skipping {skipped} instances with existing outputs, {len(instances)} remaining")
    return instances


def _serialize_results(args, agent_name: str, instances: list, results: list, extra: dict | None = None) -> dict:
    success_count = sum(1 for result in results if result.success)
    payload = {
        "agent_name": agent_name,
        "model": args.model,
        "seed": getattr(args, "run_seed", None),
        "timestamp": datetime.now().isoformat(),
        "total_instances": len(instances),
        "successful_instances": success_count,
        "success_rate": success_count / len(instances) if instances else 0,
        "results": [
            {
                "id": result.id,
                "instruction": result.instruction,
                "success": result.success,
                "error": result.error,
                "test_cases": [
                    {
                        "input_file": tc.input_file,
                        "output_file": tc.output_file,
                        "success": tc.success,
                        "agent_answer": tc.agent_answer,
                        "turns": tc.turns,
                        "error": tc.error,
                    }
                    for tc in result.test_cases
                ],
            }
            for result in results
        ],
    }
    if extra:
        payload.update(extra)
    return payload


def run_sequential(args):
    if args.working_dir:
        working_dir = args.working_dir
        os.makedirs(working_dir, exist_ok=True)
    else:
        working_dir = tempfile.mkdtemp(prefix="spreadsheetbench_")

    agent = create_agent(args)
    runner = SpreadsheetBenchRunner(
        agent=agent,
        data_path=args.data_path,
        output_dir=args.output_dir,
        working_dir=working_dir,
    )

    print(f"Using agent: {agent.name}")
    print(f"Model: {args.model}")
    if getattr(args, "run_seed", None) is not None:
        print(f"Seed: {args.run_seed}")

    all_instances = runner.load_data()
    end_idx = args.end_idx if args.end_idx is not None else len(all_instances)
    instances = all_instances[args.start_idx:end_idx]
    instances = _prepare_instances(instances, args.shuffle_seed, args.sample)
    instances = filter_instances(instances, args, args.data_path)

    if not instances:
        print("No instances to run after filtering.")
        return

    results = []
    for i, instance in enumerate(instances, start=1):
        print(f"Instance {i}/{len(instances)}: {instance.id}")
        result = runner.run_instance(instance)
        results.append(result)
        print(f"  {instance.id}: {'SUCCESS' if result.success else 'FAILED'}")

    results_file = args.results_file or os.path.join(args.output_dir, "results.json")
    os.makedirs(os.path.dirname(results_file) or ".", exist_ok=True)
    payload = _serialize_results(
        args,
        agent.name,
        instances,
        results,
        extra={
            "filtered_run": True,
            "instance_ids": args.instance_ids,
            "missing_only": args.missing_only,
        },
    )
    with open(results_file, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    print(f"\nResults saved to: {results_file}")


def run_worker(worker_id, instances, args, working_dir, progress_callback=None):
    agent = create_agent(args)
    runner = SpreadsheetBenchRunner(
        agent=agent,
        data_path=args.data_path,
        output_dir=args.output_dir,
        working_dir=working_dir,
    )
    results = []
    for instance in instances:
        try:
            result = runner.run_instance(instance)
            results.append((instance.id, result))
            if progress_callback:
                progress_callback(instance.id, result.success)
        except Exception as exc:
            from spreadsheet_agent.runner import InstanceResult

            results.append((
                instance.id,
                InstanceResult(
                    id=instance.id,
                    instruction=instance.instruction,
                    success=False,
                    error=str(exc),
                ),
            ))
            tqdm.write(f"[Worker {worker_id}] {instance.id}: ERROR - {exc}")
            if progress_callback:
                progress_callback(instance.id, False)
    return results


def run_parallel(args):
    print(f"Running in parallel mode with {args.workers} workers")
    agent = create_agent(args)
    runner = SpreadsheetBenchRunner(
        agent=agent,
        data_path=args.data_path,
        output_dir=args.output_dir,
    )
    all_instances = runner.load_data()
    end_idx = args.end_idx if args.end_idx is not None else len(all_instances)
    instances = all_instances[args.start_idx:end_idx]
    instances = _prepare_instances(instances, args.shuffle_seed, args.sample)
    instances = filter_instances(instances, args, args.data_path)
    if not instances:
        print("No instances to run after filtering.")
        return

    os.makedirs(args.output_dir, exist_ok=True)
    working_dir = args.working_dir or tempfile.mkdtemp(prefix="spreadsheetbench_parallel_")
    os.makedirs(working_dir, exist_ok=True)

    num_workers = min(args.workers, len(instances))
    chunks = [[] for _ in range(num_workers)]
    for i, instance in enumerate(instances):
        chunks[i % num_workers].append(instance)

    all_results = {}
    success_count_live = [0]
    progress_lock = threading.Lock()
    pbar = tqdm(total=len(instances), desc="Processing", unit="instance")

    def progress_callback(instance_id, success):
        del instance_id
        with progress_lock:
            if success:
                success_count_live[0] += 1
            pbar.set_postfix(success=success_count_live[0], refresh=False)
            pbar.update(1)

    start_time = datetime.now()
    with ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = {
            executor.submit(run_worker, i, chunk, args, working_dir, progress_callback): i
            for i, chunk in enumerate(chunks)
        }
        for future in as_completed(futures):
            worker_id = futures[future]
            try:
                for instance_id, result in future.result():
                    all_results[instance_id] = result
            except Exception as exc:
                tqdm.write(f"[Worker {worker_id}] Failed with error: {exc}")
    pbar.close()

    elapsed = (datetime.now() - start_time).total_seconds()
    ordered_results = [all_results[instance.id] for instance in instances if instance.id in all_results]
    results_file = args.results_file or os.path.join(args.output_dir, "results.json")
    payload = _serialize_results(
        args,
        agent.name,
        instances,
        ordered_results,
        extra={
            "parallel_workers": num_workers,
            "elapsed_seconds": elapsed,
        },
    )
    with open(results_file, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    print(f"\nResults saved to: {results_file}")


def main():
    parser = build_arg_parser()
    args = parser.parse_args()
    validate_args(parser, args)

    if not os.getenv("OPENAI_API_KEY") and args.llm_client == "openai":
        print("ERROR: OPENAI_API_KEY environment variable not set")
        sys.exit(1)

    if args.repeat > 1 and not args.seeds:
        args.num_random_seeds = args.repeat

    run_seeds = _resolve_run_seeds(args)
    if len(run_seeds) == 1:
        args.run_seed = run_seeds[0]
        if args.run_seed is not None:
            random.seed(args.run_seed)
        if args.workers > 1:
            run_parallel(args)
        else:
            run_sequential(args)
        return

    base_output_dir = args.output_dir
    base_results_file = args.results_file
    base_working_dir = args.working_dir
    base_log_dir = args.log_dir

    print(f"Running {len(run_seeds)} seeded runs: {', '.join(str(seed) for seed in run_seeds)}")
    for seed in run_seeds:
        run_args = argparse.Namespace(**vars(args))
        run_args.run_seed = seed
        run_args.output_dir = os.path.join(base_output_dir, f"seed_{seed}")
        run_args.results_file = _results_file_for_seed(base_results_file, seed)
        if base_working_dir:
            run_args.working_dir = os.path.join(base_working_dir, f"seed_{seed}")
        if base_log_dir:
            run_args.log_dir = os.path.join(base_log_dir, f"seed_{seed}")
        random.seed(seed)
        if run_args.workers > 1:
            run_parallel(run_args)
        else:
            run_sequential(run_args)


if __name__ == "__main__":
    main()
