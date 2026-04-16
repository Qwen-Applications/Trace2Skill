#!/usr/bin/env python3
"""
CLI runner for success-only parallel skill evolution.
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from pathlib import Path

from src.react_agent.models import ApiChatClient, OpenAIClient
from skill_evolver.parallel_success_evolving_agent import SuccessParallelSkillEvolver
from skill_evolver.run_parallel_skill_evolution import (
    _build_generation_config,
    _compute_dir_diff,
    _filter_records_by_task_ids,
    _load_dataset_task_ids,
    _sample_records_by_task_id,
    backup_skill,
)
from skill_evolver.skill_evolving_agent import QUICK_VALIDATE_SCRIPT

log = logging.getLogger(__name__)


def detect_success_input_mode(raw_input: object, requested_mode: str) -> str:
    if requested_mode != "auto":
        return requested_mode
    if isinstance(raw_input, list):
        return "records"
    if isinstance(raw_input, dict) and "success_memory" in raw_input:
        return "patterns"
    raise ValueError("Cannot auto-detect success input format. Use --input-mode to specify.")


def load_success_records(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, list):
        raise ValueError(f"Expected a JSON list, got {type(data).__name__}")
    return data


def load_success_patterns(path: Path) -> dict[str, list[dict]]:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"Expected a JSON object, got {type(data).__name__}")
    return data


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evolve a skill from parsed success-analysis records",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--input-json", required=True, type=Path, help="Path to parsed success analysis JSON")
    parser.add_argument("--skill-dir", required=True, type=Path, help="Path to skill directory to evolve")
    parser.add_argument(
        "--data-path",
        type=Path,
        default=None,
        help="SpreadsheetBench dataset path used to derive the task-id sample pool",
    )
    parser.add_argument("--model", required=True, help="LLM model name")
    parser.add_argument(
        "--llm-client",
        dest="llm_client",
        type=str,
        default="openai",
        choices=["openai", "api_chat"],
        help="LLM client backend to use",
    )
    parser.add_argument(
        "--api-chat-config",
        dest="api_chat_config",
        type=str,
        default="config/llm_api.json",
        help="Path to ApiChat config JSON when --llm-client=api_chat",
    )
    parser.add_argument("--base-url", default=None, help="OpenAI-compatible API base URL")
    parser.add_argument("--api-key", default=None, help="API key")
    parser.add_argument("--generation-config", type=str, default=None, help="Generation config as JSON string or path")
    parser.add_argument("--seed", type=int, default=None, help="Seed merged into generation config")
    parser.add_argument("--cache-path", type=Path, default=None, help="Disk cache path for LLM responses")
    parser.add_argument("--batch-size", type=int, default=1, help="Records per MAP phase LLM call")
    parser.add_argument("--merge-batch-size", type=int, default=5, help="Patches per MERGE phase LLM call")
    parser.add_argument("--max-workers", type=int, default=4, help="ThreadPoolExecutor parallelism")
    parser.add_argument("--max-merge-levels", type=int, default=5, help="Safety cap on hierarchical merge levels")
    parser.add_argument(
        "--start-idx",
        type=int,
        default=None,
        help="Start index (inclusive) for slicing records, or dataset tasks when --data-path is provided",
    )
    parser.add_argument(
        "--end-idx",
        type=int,
        default=None,
        help="End index (exclusive) for slicing records, or dataset tasks when --data-path is provided",
    )
    parser.add_argument(
        "--shuffle-seed",
        type=int,
        default=None,
        help="Shuffle record order, or dataset task order when --data-path is provided, with a fixed seed",
    )
    parser.add_argument(
        "--sample-task-count",
        type=int,
        default=None,
        help="Take this many task ids from the dataset-derived pool and keep only lessons from those tasks",
    )
    parser.add_argument(
        "--sample-task-seed",
        type=int,
        default=None,
        help="Deprecated alias for --shuffle-seed when sampling by dataset task ids",
    )
    parser.add_argument("--continue-evolving", action="store_true", help="Continue evolving without creating a backup")
    parser.add_argument("--dry-run", action="store_true", help="Show changes without writing to disk")
    parser.add_argument("--max-skill-lines", type=int, default=500, help="Max SKILL.md lines")
    parser.add_argument("--temperature", type=float, default=0.6, help="LLM temperature")
    parser.add_argument("--max-tokens", type=int, default=None, help="Max generation tokens for LLM responses")
    parser.add_argument("--verbose", action="store_true", help="Print detailed progress")
    parser.add_argument("--output-dir", type=Path, default=None, help="Copy final skill here")
    parser.add_argument("--save-intermediates", action="store_true", help="Save intermediate artifacts")
    parser.add_argument(
        "--parse-failure-dir",
        type=Path,
        default=Path("parse_failures_parallel_success"),
        help="Directory to save parse-failed LLM artifacts",
    )
    parser.add_argument("--intermediates-dir", type=Path, default=None, help="Directory for intermediate artifacts")
    parser.add_argument("--changelog", type=Path, default=None, help="Write changelog to file")
    parser.add_argument("--patch-file", type=Path, default=None, help="Write cumulative unified diff to file")
    parser.add_argument(
        "--input-mode",
        type=str,
        default="auto",
        choices=["records", "patterns", "auto"],
        help="Input format: 'records', 'patterns', or 'auto' (detect from JSON)",
    )
    parser.add_argument("--skip-translation", action="store_true", help="Skip TRANSLATION phase")
    parser.add_argument("--patch-pipeline", type=str, default="json", choices=["json", "markdown"])
    parser.add_argument(
        "--semantic-item-marker-format",
        type=str,
        default="bracket",
        choices=["bracket", "heading"],
        help="Item marker syntax for markdown semantic patches",
    )
    parser.add_argument("--disable-json-format-self-fix", action="store_true")
    args = parser.parse_args()
    generation_config = _build_generation_config(args)

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if not args.input_json.exists():
        log.error("Input JSON not found: %s", args.input_json)
        sys.exit(1)
    if args.data_path is not None and not args.data_path.exists():
        log.error("Dataset path not found: %s", args.data_path)
        sys.exit(1)
    if not args.skill_dir.is_dir():
        log.error("Skill directory not found: %s", args.skill_dir)
        sys.exit(1)
    if not (args.skill_dir / "SKILL.md").exists():
        log.error("SKILL.md not found in %s", args.skill_dir)
        sys.exit(1)
    if args.sample_task_seed is not None:
        if args.shuffle_seed is not None and args.shuffle_seed != args.sample_task_seed:
            log.error(
                "--sample-task-seed and --shuffle-seed disagree. Use only one or provide the same value."
            )
            sys.exit(1)
        args.shuffle_seed = args.sample_task_seed

    with open(args.input_json, encoding="utf-8") as fh:
        raw_input = json.load(fh)
    input_mode = detect_success_input_mode(raw_input, args.input_mode)
    if input_mode == "records":
        if not isinstance(raw_input, list):
            raise ValueError(f"Expected a JSON list for records mode, got {type(raw_input).__name__}")
        records = raw_input
        use_dataset_task_pool = args.data_path is not None

        if args.shuffle_seed is not None and not use_dataset_task_pool:
            import random

            rng = random.Random(args.shuffle_seed)
            records = list(records)
            rng.shuffle(records)

        if (args.start_idx is not None or args.end_idx is not None) and not use_dataset_task_pool:
            start = args.start_idx or 0
            end = args.end_idx if args.end_idx is not None else len(records)
            records = records[start:end]
        if use_dataset_task_pool:
            selected_task_ids = _load_dataset_task_ids(
                args.data_path,
                start_idx=args.start_idx,
                end_idx=args.end_idx,
                shuffle_seed=args.shuffle_seed,
                sample_task_count=args.sample_task_count,
            )
            original_record_count = len(records)
            records = _filter_records_by_task_ids(records, set(selected_task_ids))
            log.info(
                "Selected %d dataset task ids and kept %d/%d success records",
                len(selected_task_ids),
                len(records),
                original_record_count,
            )
        elif args.sample_task_count is not None:
            original_record_count = len(records)
            records, sampled_task_ids = _sample_records_by_task_id(
                records,
                sample_task_count=args.sample_task_count,
                sample_task_seed=args.shuffle_seed,
            )
            log.info(
                "Sampled %d unique task ids from success records and kept %d/%d records",
                len(sampled_task_ids),
                len(records),
                original_record_count,
            )
        payload = records
        log.info("Loaded %d success records from %s", len(records), args.input_json)
    else:
        if not isinstance(raw_input, dict):
            raise ValueError(f"Expected a JSON object for patterns mode, got {type(raw_input).__name__}")
        payload = raw_input
        total_patterns = sum(len(v) for v in payload.values() if isinstance(v, list))
        log.info("Loaded %d success patterns from %s", total_patterns, args.input_json)

    backup_path = None
    if args.continue_evolving:
        log.info("Continuing evolution without backup (--continue-evolving)")
    elif not args.dry_run:
        backup_path = backup_skill(args.skill_dir)
        log.info("Backed up skill to %s", backup_path)
    else:
        log.info("[DRY RUN] Skipping backup")

    intermediates_dir = None
    if args.save_intermediates:
        intermediates_dir = args.intermediates_dir or (
            args.skill_dir.parent / f"{args.skill_dir.name}_parallel_success_output"
        )
        intermediates_dir.mkdir(parents=True, exist_ok=True)

    client_kwargs: dict = {"model": args.model}
    if args.base_url:
        client_kwargs["base_url"] = args.base_url
    if args.api_key:
        client_kwargs["api_key"] = args.api_key
    if args.cache_path:
        client_kwargs["cache_path"] = str(args.cache_path)
    if generation_config:
        client_kwargs["generation_config"] = generation_config
    if args.llm_client == "api_chat":
        client_kwargs.pop("base_url", None)
        client_kwargs.pop("api_key", None)
        client_kwargs["config_path"] = args.api_chat_config
        client = ApiChatClient(**client_kwargs)
    else:
        client = OpenAIClient(**client_kwargs)

    evolver = SuccessParallelSkillEvolver(
        client=client,
        skill_dir=args.skill_dir,
        batch_size=args.batch_size,
        merge_batch_size=args.merge_batch_size,
        max_workers=args.max_workers,
        max_merge_levels=args.max_merge_levels,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        verbose=args.verbose,
        dry_run=args.dry_run,
        output_dir=intermediates_dir,
        parse_failure_dir=args.parse_failure_dir,
        max_skill_lines=args.max_skill_lines,
        skip_translation=args.skip_translation,
        patch_pipeline=args.patch_pipeline,
        semantic_item_marker_format=args.semantic_item_marker_format,
        enable_json_format_self_fix=not args.disable_json_format_self_fix,
    )

    if not args.dry_run and not QUICK_VALIDATE_SCRIPT.exists():
        log.error("Skill format checker not found at %s", QUICK_VALIDATE_SCRIPT)
        sys.exit(1)

    result = evolver.run(payload, input_mode=input_mode)

    print("\n" + "=" * 60)
    print("PARALLEL SUCCESS EVOLUTION SUMMARY")
    print("=" * 60)
    n_patches = len(result.get("patches", []))
    print(f"MAP patches produced:  {n_patches}")
    print(f"LLM calls (est):      {result.get('total_llm_calls', 0)}")
    print(f"Edits applied:         {len(result.get('edits', []))}")
    print(f"Reasoning:             {result.get('reasoning', '')[:200]}")

    diffs = result.get("diffs", [])
    if diffs:
        print("\nDiffs:")
        for diff in diffs:
            if diff.unified_diff:
                print(f"\n--- {diff.relative_path} ({diff.action}) ---")
                print(diff.unified_diff)
    else:
        print("\nDiffs: (no changes)")

    if backup_path and not args.dry_run:
        final_diffs = _compute_dir_diff(backup_path, args.skill_dir)
        if final_diffs:
            print("\nFinal vs Original Diff:")
            for diff_text in final_diffs:
                print(diff_text)

    cumulative_patch = result.get("cumulative_patch", "")
    if args.patch_file and cumulative_patch:
        args.patch_file.write_text(cumulative_patch, encoding="utf-8")
    if args.changelog:
        final_diffs = _compute_dir_diff(backup_path, args.skill_dir) if backup_path else []
        args.changelog.write_text("\n".join(final_diffs), encoding="utf-8")
    if args.output_dir:
        if args.output_dir.exists():
            shutil.rmtree(args.output_dir)
        shutil.copytree(args.skill_dir, args.output_dir)


if __name__ == "__main__":
    main()
