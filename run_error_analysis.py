#!/usr/bin/env python3
"""
CLI orchestration script for running error analysis on SpreadsheetBench instances.

Sets up the analysis workspace for each instance and delegates to the
error analysis agent (analysis/error_analysis_agent.py).
"""

from __future__ import annotations

import argparse
import glob
import inspect
import json
import os
import shutil
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from tqdm import tqdm

# Ensure project root is on sys.path so imports work
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

import evaluate_outputs
from analysis.error_analysis_agent import run_error_analysis
from analysis.error_analysis_agent_tolerant import run_error_analysis_tolerant


def parse_generation_config(generation_config: str | None) -> dict:
    """Parse generation config from JSON string or JSON file path."""
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


def build_generation_config(args) -> dict:
    """Build generation config and merge seed config."""
    generation_config = parse_generation_config(args.generation_config)
    seed_config = {"seed": args.seed} if args.seed is not None else {}
    generation_config.update(seed_config)
    return generation_config


def _call_with_supported_kwargs(func, **kwargs):
    """Call a function while dropping kwargs unsupported by older implementations."""
    parameters = inspect.signature(func).parameters
    filtered_kwargs = {key: value for key, value in kwargs.items() if key in parameters}
    return func(**filtered_kwargs)


def find_log_file(logs_dir: str, instance_id: str) -> str | None:
    """Find the log file for an instance in the logs directory."""
    patterns = [
        os.path.join(logs_dir, f"*_{instance_id}_*.md"),
        os.path.join(logs_dir, f"*_{instance_id}.md"),
    ]
    for pattern in patterns:
        matches = glob.glob(pattern)
        if matches:
            return matches[0]
    return None


def find_work_dir(work_dir: str, instance_id: str) -> str | None:
    """Find the work directory for an instance."""
    pattern = os.path.join(work_dir, f"{instance_id}_*")
    matches = glob.glob(pattern)
    if matches:
        return matches[0]
    # Also try exact match (in case the dir is just the instance_id)
    exact = os.path.join(work_dir, instance_id)
    if os.path.isdir(exact):
        return exact
    return None


def build_instance_index(data_path: str) -> dict[str, dict]:
    """Load the dataset once and return an id->item mapping."""
    dataset = evaluate_outputs.load_dataset(data_path)
    return {str(item.get("id")): item for item in dataset if "id" in item}


def find_gold_file(data_path: str, instance: dict) -> str | None:
    """Find the ground-truth file for an instance using evaluate_outputs helpers."""
    # Ensure id is a string — evaluate_outputs.find_spreadsheet_dir uses it in os.path.join
    inst = {**instance, "id": str(instance["id"])}
    spreadsheet_dir = evaluate_outputs.find_spreadsheet_dir(data_path, inst)
    if spreadsheet_dir is None:
        return None

    all_files = os.listdir(spreadsheet_dir)
    # Prefer *_golden.xlsx, then *_answer.xlsx, then golden.xlsx
    for suffix in ("_golden.xlsx", "_answer.xlsx"):
        candidates = sorted(f for f in all_files if f.endswith(suffix))
        if candidates:
            return os.path.join(spreadsheet_dir, candidates[0])
    if "golden.xlsx" in all_files:
        return os.path.join(spreadsheet_dir, "golden.xlsx")
    return None


def _parse_log_filename(name: str) -> tuple[str, bool] | None:
    """
    Extract (instance_id, is_failed) from a log filename.

    Expected patterns:
        cli_only_agent_10747_FAILED.md  -> ("10747", True)
        cli_skill_agent_105-24_SUCCEED.md -> ("105-24", False)
    """
    if not name.endswith(".md"):
        return None
    stem = name.rsplit(".", 1)[0]
    is_failed = stem.endswith("_FAILED")
    for tag in ("_SUCCEED", "_FAILED"):
        if stem.endswith(tag):
            stem = stem[: -len(tag)]
            break
    parts = stem.split("_")
    if not parts:
        return None
    return parts[-1], is_failed


def get_instance_ids(args) -> list[str]:
    """Determine the list of instance IDs to process."""
    if args.instance_ids:
        return [s.strip() for s in args.instance_ids.split(",")]

    # Discover from log filenames
    ids = []
    seen = set()
    for name in sorted(os.listdir(args.logs_dir)):
        parsed = _parse_log_filename(name)
        if parsed is None:
            continue
        instance_id, is_failed = parsed
        if instance_id in seen:
            continue
        if not is_failed:
            continue
        seen.add(instance_id)
        ids.append(instance_id)
    return ids


def get_repeat_instances(args) -> list[tuple[str, str, str]]:
    """
    Discover instances from seed_* subdirectories of args.logs_dir.

    Returns list of (composite_id, original_id, log_path) where
    composite_id = "{original_id}_{seed_name}" (e.g. "10747_seed_42").
    """
    result = []
    seen = set()
    for entry in sorted(os.scandir(args.logs_dir)):
        if not (entry.is_dir() and entry.name.startswith("seed_")):
            continue
        seed_name = entry.name
        for name in sorted(os.listdir(entry.path)):
            parsed = _parse_log_filename(name)
            if parsed is None:
                continue
            original_id, is_failed = parsed
            if not is_failed:
                continue
            composite_id = f"{original_id}_{seed_name}"
            if composite_id in seen:
                continue
            seen.add(composite_id)
            log_path = os.path.join(entry.path, name)
            result.append((composite_id, original_id, log_path))
    return result


def setup_analysis_dir(
    output_dir: str,
    instance_id: str,
    log_path: str,
    agent_work_path: str | None,
    gold_path: str | None,
) -> str:
    """
    Create the analysis workspace for a single instance.

    Layout:
        {output_dir}/{instance_id}/
            agent_log.md
            agent_work/
                input.xlsx
                output.xlsx
                gold.xlsx
                (other files from agent work dir)
    """
    analysis_dir = os.path.join(output_dir, instance_id)
    agent_work_dest = os.path.join(analysis_dir, "agent_work")
    os.makedirs(agent_work_dest, exist_ok=True)

    # Save raw markdown log for reference
    raw_log = Path(log_path).read_text(encoding="utf-8", errors="replace")
    agent_log_dest = os.path.join(analysis_dir, "agent_log.md")
    Path(agent_log_dest).write_text(raw_log, encoding="utf-8")

    # Copy agent work dir contents
    if agent_work_path and os.path.isdir(agent_work_path):
        for item in os.listdir(agent_work_path):
            src = os.path.join(agent_work_path, item)
            dst = os.path.join(agent_work_dest, item)
            if os.path.isfile(src):
                shutil.copy2(src, dst)
            elif os.path.isdir(src):
                if os.path.exists(dst):
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)

    # Copy gold file
    if gold_path and os.path.isfile(gold_path):
        shutil.copy2(gold_path, os.path.join(agent_work_dest, "gold.xlsx"))

    return analysis_dir


def run_single_instance(
    instance_id: str,
    args,
    instance_index: dict[str, dict],
    print_lock: threading.Lock | None = None,
    log_path_override: str | None = None,
    dataset_id: str | None = None,
    work_dir_override: str | None = None,
) -> dict:
    """Run error analysis for one instance. Returns a result dict.

    Args:
        instance_id: ID used for output directory naming (may be composite like
            "10747_seed_42" in repeat mode).
        log_path_override: Pre-resolved log file path (used in repeat mode to avoid
            re-discovery inside seed subdirectories).
        dataset_id: Original instance ID for dataset metadata lookup (defaults to
            instance_id when not provided).
        work_dir_override: Work directory to search for agent work artifacts (used in
            repeat mode when work dirs are organised in seed subdirectories).
    """
    def log(msg: str) -> None:
        if not getattr(args, "verbose", False):
            return
        if print_lock:
            with print_lock:
                print(msg)
        else:
            print(msg)

    analysis_dir = os.path.join(args.output_dir, instance_id)
    existing_report = os.path.join(analysis_dir, "analysis_report.md")
    if os.path.isfile(existing_report):
        log(f"\n{'='*60}")
        log(f"Instance: {instance_id}")
        log(f"{'='*60}")
        log(f"  SKIP: Existing analysis report found at {existing_report}")
        return {"id": instance_id, "skipped": True, "error": None, "report": existing_report}

    log(f"\n{'='*60}")
    log(f"Instance: {instance_id}")
    log(f"{'='*60}")

    # Resolve paths and metadata
    log_path = log_path_override or find_log_file(args.logs_dir, instance_id)
    if not log_path:
        log(f"  SKIP: No log file found for {instance_id}")
        return {"id": instance_id, "skipped": True, "error": "missing_log"}

    effective_work_dir = work_dir_override or args.work_dir
    lookup_id = dataset_id or instance_id
    agent_work_path = find_work_dir(effective_work_dir, lookup_id)
    if not agent_work_path:
        log(f"  WARNING: No work directory found for {instance_id}")

    instance_meta = instance_index.get(str(lookup_id))
    answer_position = None
    gold_path = None
    if instance_meta:
        answer_position = instance_meta.get("answer_position") or None
        gold_path = find_gold_file(args.data_path, instance_meta)
    else:
        log(f"  WARNING: Instance {instance_id} not found in dataset")

    if not gold_path:
        log(f"  WARNING: No gold file found for {instance_id}")

    # Set up analysis workspace
    analysis_dir = setup_analysis_dir(
        output_dir=args.output_dir,
        instance_id=instance_id,
        log_path=log_path,
        agent_work_path=agent_work_path,
        gold_path=gold_path,
    )
    log(f"  Analysis dir: {analysis_dir}")

    # Read log content
    log_content = Path(log_path).read_text(encoding="utf-8", errors="replace")

    # Run the analysis agent
    try:
        if args.tolerant:
            report = _call_with_supported_kwargs(
                run_error_analysis_tolerant,
                analysis_dir=os.path.abspath(analysis_dir),
                agent_log_content=log_content,
                model=args.model,
                answer_position=answer_position,
                max_turns=args.max_turns,
                base_url=args.base_url,
                api_key=args.api_key,
                generation_config=args.generation_config_dict,
                llm_client=args.llm_client,
                api_chat_config=args.api_chat_config,
                verbose=args.verbose,
            )
        else:
            report = _call_with_supported_kwargs(
                run_error_analysis,
                analysis_dir=os.path.abspath(analysis_dir),
                agent_log_content=log_content,
                model=args.model,
                answer_position=answer_position,
                max_turns=args.max_turns,
                base_url=args.base_url,
                api_key=args.api_key,
                generation_config=args.generation_config_dict,
                llm_client=args.llm_client,
                api_chat_config=args.api_chat_config,
                use_skill_prompt=args.skill,
                verbose=args.verbose,
            )
        error = None
    except Exception as e:
        log(f"  ERROR: Analysis failed for {instance_id}: {e}")
        report = f"Analysis failed with error: {e}"
        error = str(e)

    # Save report
    report_path = os.path.join(analysis_dir, "analysis_report.md")
    Path(report_path).write_text(report, encoding="utf-8")
    log(f"  Report saved: {report_path}")

    return {"id": instance_id, "skipped": False, "error": error, "report": report_path}


def main():
    parser = argparse.ArgumentParser(
        description="Run error analysis agent on SpreadsheetBench instances."
    )
    parser.add_argument(
        "--data_path", required=True,
        help="SpreadsheetBench dataset path (e.g. data/spreadsheetbench_verified/spreadsheetbench_verified_400)",
    )
    parser.add_argument(
        "--work_dir", required=True,
        help="Target agent's work directory (e.g. agent_output/cli_only_work)",
    )
    parser.add_argument(
        "--logs_dir", required=True,
        help="Target agent's logs directory (e.g. agent_output/cli_only_logs)",
    )
    parser.add_argument(
        "--output_dir", default="analysis_output",
        help="Where to save analysis results (default: analysis_output/)",
    )
    parser.add_argument(
        "--instance_ids", default=None,
        help="Comma-separated specific instance IDs to analyze",
    )
    parser.add_argument("--model", required=True, help="Model name (OpenAI-compatible)")
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
    parser.add_argument("--base_url", default=None, help="OpenAI-compatible base URL")
    parser.add_argument("--api_key", default=None, help="API key (or use OPENAI_API_KEY)")
    parser.add_argument(
        "--generation_config",
        type=str,
        default=None,
        help="Generation config as JSON string or path to JSON file",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Seed merged into generation config",
    )
    parser.add_argument("--max_turns", type=int, default=20, help="Max agent turns (default: 20)")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--skill", action="store_true", help="Use skill-based system prompt")
    mode_group.add_argument(
        "--tolerant",
        action="store_true",
        help=(
            "Use the tolerant analysis mode: agent diagnoses and reports the failure "
            "without needing to fix it (no PASS-gated completion check)"
        ),
    )
    parser.add_argument("--workers", type=int, default=1, help="Number of parallel workers")
    parser.add_argument("--verbose", action="store_true", help="Verbose logging")
    parser.add_argument("--sample", type=int, default=None, help="Only analyze the first N instances")
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="Number of seed runs. When > 1, discovers instances from seed_*/ subdirectories "
             "under --logs_dir and names analysis dirs as {id}_seed_{seed} (default: 1).",
    )

    args = parser.parse_args()
    args.generation_config_dict = build_generation_config(args)

    if args.repeat > 1:
        _run_repeat_error_analysis(args)
        return

    instance_ids = get_instance_ids(args)
    if args.sample is not None:
        instance_ids = instance_ids[: args.sample]

    if not instance_ids:
        print("No instances to analyze.", file=sys.stderr)
        sys.exit(1)

    if args.verbose:
        print(f"Analyzing {len(instance_ids)} instance(s): {instance_ids}")
    else:
        print(f"Analyzing {len(instance_ids)} instance(s)")
    os.makedirs(args.output_dir, exist_ok=True)

    instance_index = build_instance_index(args.data_path)

    pbar = tqdm(total=len(instance_ids), desc="Analyzing", unit="instance")
    progress_lock = threading.Lock()
    stats = {"success": 0, "failed": 0, "skipped": 0}

    def progress_update(result: dict | None = None) -> None:
        with progress_lock:
            if result is not None:
                if result.get("skipped"):
                    stats["skipped"] += 1
                elif result.get("error"):
                    stats["failed"] += 1
                else:
                    stats["success"] += 1
                pbar.set_postfix(
                    success=stats["success"],
                    failed=stats["failed"],
                    skipped=stats["skipped"],
                    refresh=False,
                )
            pbar.update(1)

    if args.workers <= 1 or len(instance_ids) <= 1:
        for instance_id in instance_ids:
            result = run_single_instance(instance_id, args, instance_index)
            progress_update(result)
        pbar.close()
        print(f"\nDone. Results in: {args.output_dir}")
        return

    num_workers = min(args.workers, len(instance_ids))
    if args.verbose:
        print(f"Running in parallel mode with {num_workers} workers")
    print_lock = threading.Lock()
    results = []

    with ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = {
            executor.submit(run_single_instance, instance_id, args, instance_index, print_lock): instance_id
            for instance_id in instance_ids
        }
        for future in as_completed(futures):
            instance_id = futures[future]
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                if args.verbose:
                    with print_lock:
                        print(f"  ERROR: Worker failed for {instance_id}: {e}")
                result = {"id": instance_id, "skipped": False, "error": str(e)}
                results.append(result)
            progress_update(result)

    pbar.close()

    skipped = sum(1 for r in results if r.get("skipped"))
    failed = sum(1 for r in results if r.get("error"))
    print(f"\nDone. Results in: {args.output_dir}")
    print(f"Summary: total={len(instance_ids)} skipped={skipped} failed={failed}")


def _run_repeat_error_analysis(args) -> None:
    """Repeat-mode driver: discover instances from seed_* subdirs of logs_dir."""
    repeat_instances = get_repeat_instances(args)
    if args.sample is not None:
        repeat_instances = repeat_instances[: args.sample]

    if not repeat_instances:
        print("No instances to analyze.", file=sys.stderr)
        sys.exit(1)

    print(f"Analyzing {len(repeat_instances)} instance(s) from seed subdirectories")
    os.makedirs(args.output_dir, exist_ok=True)
    instance_index = build_instance_index(args.data_path)

    pbar = tqdm(total=len(repeat_instances), desc="Analyzing", unit="instance")
    progress_lock = threading.Lock()
    stats = {"success": 0, "failed": 0, "skipped": 0}

    def progress_update(result: dict | None = None) -> None:
        with progress_lock:
            if result is not None:
                if result.get("skipped"):
                    stats["skipped"] += 1
                elif result.get("error"):
                    stats["failed"] += 1
                else:
                    stats["success"] += 1
                pbar.set_postfix(
                    success=stats["success"],
                    failed=stats["failed"],
                    skipped=stats["skipped"],
                    refresh=False,
                )
            pbar.update(1)

    def run_one(composite_id: str, original_id: str, log_path: str) -> dict:
        # Work dir may be in seed subdir: derive from log_path's parent
        seed_dir = os.path.dirname(log_path)  # e.g. logs/seed_42
        seed_work_dir = None
        if args.work_dir:
            # Try {work_dir}/{seed_name} first
            seed_name = os.path.basename(seed_dir)
            candidate = os.path.join(args.work_dir, seed_name)
            seed_work_dir = candidate if os.path.isdir(candidate) else args.work_dir
        return run_single_instance(
            instance_id=composite_id,
            args=args,
            instance_index=instance_index,
            log_path_override=log_path,
            dataset_id=original_id,
            work_dir_override=seed_work_dir,
        )

    num_workers = min(args.workers, len(repeat_instances))
    if num_workers <= 1:
        for composite_id, original_id, log_path in repeat_instances:
            result = run_one(composite_id, original_id, log_path)
            progress_update(result)
        pbar.close()
        print(f"\nDone. Results in: {args.output_dir}")
        return

    print_lock = threading.Lock()
    results = []
    with ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = {
            executor.submit(run_one, composite_id, original_id, log_path): composite_id
            for composite_id, original_id, log_path in repeat_instances
        }
        for future in as_completed(futures):
            composite_id = futures[future]
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                if args.verbose:
                    with print_lock:
                        print(f"  ERROR: Worker failed for {composite_id}: {e}")
                result = {"id": composite_id, "skipped": False, "error": str(e)}
                results.append(result)
            progress_update(result)

    pbar.close()
    skipped = sum(1 for r in results if r.get("skipped"))
    failed = sum(1 for r in results if r.get("error"))
    print(f"\nDone. Results in: {args.output_dir}")
    print(f"Summary: total={len(repeat_instances)} skipped={skipped} failed={failed}")


if __name__ == "__main__":
    main()
