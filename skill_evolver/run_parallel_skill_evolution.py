#!/usr/bin/env python3
"""
CLI runner for the Parallel Skill Evolving Agent.

Uses a map-reduce pipeline: each error record batch independently proposes
a concise patch against the frozen original skill, patches are merged
hierarchically, and the final merged patch is applied.

Usage:
    python -m skill_evolver.run_parallel_skill_evolution \
        --input-json skill_preloaded_error_skill_focused.json \
        --skill-dir spreadsheet_agent/skills/xlsx/ \
        --model gpt-4o \
        --api-key $OPENAI_API_KEY

    # With intermediate artifacts saved:
    python -m skill_evolver.run_parallel_skill_evolution \
        --input-json skill_preloaded_error_skill_focused.json \
        --skill-dir spreadsheet_agent/skills/xlsx/ \
        --model gpt-4o \
        --save-intermediates \
        --intermediates-dir parallel_output/
"""

from __future__ import annotations

import argparse
import difflib
import json
import logging
import random
import shutil
import sys
from datetime import datetime
from pathlib import Path

import evaluate_outputs
from src.react_agent.models import ApiChatClient, OpenAIClient
from skill_evolver.skill_evolving_agent import PROMPT_VARIANTS, QUICK_VALIDATE_SCRIPT
from skill_evolver.parallel_evolving_agent import ParallelSkillEvolver

log = logging.getLogger(__name__)


def _parse_generation_config(generation_config: str | None) -> dict:
    """Parse generation config from JSON string or JSON file path."""
    if not generation_config:
        return {}
    gc_path = Path(generation_config)
    if gc_path.is_file():
        with open(gc_path, encoding="utf-8") as fp:
            parsed = json.load(fp)
    else:
        parsed = json.loads(generation_config)
    if not isinstance(parsed, dict):
        raise ValueError("--generation-config must be a JSON object or a path to a JSON object file")
    return parsed


def _build_generation_config(args) -> dict:
    """Build generation config and merge seed config."""
    generation_config = _parse_generation_config(args.generation_config)
    seed_config = {"seed": args.seed} if args.seed is not None else {}
    generation_config.update(seed_config)
    return generation_config


def _normalize_base_task_id(raw_id: object) -> str | None:
    """Normalize parsed-record ids so prompt variants map back to one task id."""
    if raw_id is None:
        return None
    normalized = str(raw_id).strip()
    if not normalized:
        return None
    if normalized.endswith("_prompt"):
        return normalized[: -len("_prompt")]
    return normalized


def _extract_base_task_id(record: dict) -> str | None:
    """Extract the task id used for task-level sampling from a parsed record."""
    for key in ("task_id", "instance_id", "id"):
        task_id = _normalize_base_task_id(record.get(key))
        if task_id is not None:
            return task_id
    return None


def _sample_records_by_task_id(
    records: list[dict],
    sample_task_count: int,
    sample_task_seed: int | None = None,
) -> tuple[list[dict], list[str]]:
    """Sample unique task ids, then keep all records that belong to those tasks."""
    if sample_task_count <= 0:
        raise ValueError("--sample-task-count must be positive")

    ordered_task_ids: list[str] = []
    seen_task_ids: set[str] = set()
    for record in records:
        task_id = _extract_base_task_id(record)
        if task_id is None:
            raise ValueError(
                "Cannot sample by task id because at least one record is missing "
                "a usable task identifier in task_id, instance_id, or id."
            )
        if task_id not in seen_task_ids:
            seen_task_ids.add(task_id)
            ordered_task_ids.append(task_id)

    if sample_task_count >= len(ordered_task_ids):
        return records, ordered_task_ids

    rng = random.Random(sample_task_seed)
    sampled_task_ids = sorted(rng.sample(ordered_task_ids, sample_task_count))
    sampled_task_id_set = set(sampled_task_ids)
    sampled_records = [
        record
        for record in records
        if _extract_base_task_id(record) in sampled_task_id_set
    ]
    return sampled_records, sampled_task_ids


def _load_dataset_task_ids(
    data_path: Path,
    start_idx: int | None = None,
    end_idx: int | None = None,
    shuffle_seed: int | None = None,
    sample_task_count: int | None = None,
) -> list[str]:
    """Load task ids from the dataset using SpreadsheetBench runner semantics."""
    dataset = evaluate_outputs.load_dataset(str(data_path))
    end = end_idx if end_idx is not None else len(dataset)
    sliced_dataset = dataset[start_idx or 0:end]
    task_ids = [str(item.get("id")) for item in sliced_dataset if item.get("id") is not None]
    if shuffle_seed is not None:
        rng = random.Random(shuffle_seed)
        rng.shuffle(task_ids)
    if sample_task_count is not None:
        if sample_task_count <= 0:
            raise ValueError("--sample-task-count must be positive")
        task_ids = task_ids[:sample_task_count]
    return task_ids


def _filter_records_by_task_ids(
    records: list[dict],
    selected_task_ids: set[str],
) -> list[dict]:
    """Keep only parsed records whose base task id belongs to the selected task set."""
    filtered_records: list[dict] = []
    for record in records:
        task_id = _extract_base_task_id(record)
        if task_id is None:
            raise ValueError(
                "Cannot filter by dataset task ids because at least one record is missing "
                "a usable task identifier in task_id, instance_id, or id."
            )
        if task_id in selected_task_ids:
            filtered_records.append(record)
    return filtered_records


def backup_skill(skill_dir: Path) -> Path:
    """Create a timestamped backup of the skill directory."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = skill_dir.parent / f"{skill_dir.name}_backup_{timestamp}"
    shutil.copytree(skill_dir, backup_dir)
    return backup_dir


def _read_skill_files(skill_dir: Path) -> dict[str, str]:
    """Read SKILL.md + references/* into {rel_path: content}."""
    files: dict[str, str] = {}
    skill_md = skill_dir / "SKILL.md"
    if skill_md.exists():
        files["SKILL.md"] = skill_md.read_text(encoding="utf-8")
    refs_dir = skill_dir / "references"
    if refs_dir.is_dir():
        for ref_file in sorted(refs_dir.iterdir()):
            if ref_file.is_file():
                rel = f"references/{ref_file.name}"
                files[rel] = ref_file.read_text(encoding="utf-8")
    return files


def _compute_dir_diff(old_dir: Path, new_dir: Path) -> list[str]:
    """Compute unified diffs for SKILL.md + references/* between two dirs."""
    old_files = _read_skill_files(old_dir)
    new_files = _read_skill_files(new_dir)
    all_paths = sorted(set(old_files) | set(new_files))
    diffs: list[str] = []
    for rel in all_paths:
        old = old_files.get(rel, "")
        new = new_files.get(rel, "")
        if old == new:
            continue
        from_label = f"a/{rel}" if rel in old_files else "/dev/null"
        to_label = f"b/{rel}" if rel in new_files else "/dev/null"
        diff_lines = difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=from_label,
            tofile=to_label,
            lineterm="",
        )
        diffs.append("".join(diff_lines))
    return diffs


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evolve a skill using parallel map-reduce pipeline",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--input-json",
        required=True,
        type=Path,
        help="Path to parsed error analysis JSON",
    )
    parser.add_argument(
        "--skill-dir",
        required=True,
        type=Path,
        help="Path to skill directory to evolve",
    )
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
    parser.add_argument(
        "--base-url", default=None, help="OpenAI-compatible API base URL"
    )
    parser.add_argument("--api-key", default=None, help="API key")
    parser.add_argument(
        "--generation-config",
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
    parser.add_argument(
        "--cache-path",
        type=Path,
        default=None,
        help="Disk cache path for LLM responses",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=1,
        help="Records per MAP phase LLM call",
    )
    parser.add_argument(
        "--merge-batch-size",
        type=int,
        default=5,
        help="Patches per MERGE phase LLM call",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=4,
        help="ThreadPoolExecutor parallelism",
    )
    parser.add_argument(
        "--max-merge-levels",
        type=int,
        default=5,
        help="Safety cap on hierarchical merge levels",
    )
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
    parser.add_argument(
        "--continue-evolving",
        action="store_true",
        help="Continue evolving without creating a backup",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show changes without writing to disk",
    )
    parser.add_argument(
        "--max-skill-lines",
        type=int,
        default=500,
        help="Max SKILL.md lines",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.6,
        help="LLM temperature",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=None,
        help="Max generation tokens for LLM responses",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print detailed progress",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Copy final skill here",
    )
    parser.add_argument(
        "--save-intermediates",
        action="store_true",
        help="Save intermediate artifacts (map patches, merge results)",
    )
    parser.add_argument(
        "--parse-failure-dir",
        type=Path,
        default=Path("parse_failures_parallel"),
        help="Directory to save parse-failed LLM prompt/response artifacts",
    )
    parser.add_argument(
        "--intermediates-dir",
        type=Path,
        default=None,
        help="Directory for intermediate artifacts (default: {skill-dir}_parallel_output/)",
    )
    parser.add_argument(
        "--changelog",
        type=Path,
        default=None,
        help="Write changelog to file",
    )
    parser.add_argument(
        "--patch-file",
        type=Path,
        default=None,
        help="Write cumulative unified diff to file",
    )
    parser.add_argument(
        "--prompt",
        type=str,
        default="skill",
        choices=list(PROMPT_VARIANTS.keys()),
        help="Prompt variant for MAP phase",
    )
    parser.add_argument(
        "--input-mode",
        type=str,
        default="auto",
        choices=["records", "patterns", "auto"],
        help="Input format: 'records', 'patterns', or 'auto' (detect from JSON)",
    )
    parser.add_argument(
        "--skip-translation",
        action="store_true",
        help="Skip TRANSLATION phase and apply merged edits directly",
    )
    parser.add_argument(
        "--patch-pipeline",
        type=str,
        default="json",
        choices=["json", "markdown"],
        help="Patch pipeline format: strict JSON or semantic markdown",
    )
    parser.add_argument(
        "--semantic-item-marker-format",
        type=str,
        default="bracket",
        choices=["bracket", "heading"],
        help="Item marker syntax for markdown semantic patches",
    )
    parser.add_argument(
        "--disable-json-format-self-fix",
        action="store_true",
        help="Disable JSON format-fix retry prompts and rely on direct/heuristic parsing",
    )
    args = parser.parse_args()
    generation_config = _build_generation_config(args)

    # Setup logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    if args.verbose:
        for noisy_logger in ("openai", "httpx", "httpcore", "urllib3"):
            logging.getLogger(noisy_logger).setLevel(logging.WARNING)

    # Validate inputs
    if not args.input_json.exists():
        log.error("Input JSON not found: %s", args.input_json)
        sys.exit(1)
    if not args.skill_dir.is_dir():
        log.error("Skill directory not found: %s", args.skill_dir)
        sys.exit(1)
    if not (args.skill_dir / "SKILL.md").exists():
        log.error("SKILL.md not found in %s", args.skill_dir)
        sys.exit(1)
    if args.data_path is not None and not args.data_path.exists():
        log.error("Dataset path not found: %s", args.data_path)
        sys.exit(1)
    if args.sample_task_seed is not None:
        if args.shuffle_seed is not None and args.shuffle_seed != args.sample_task_seed:
            log.error(
                "--sample-task-seed and --shuffle-seed disagree. Use only one or provide the same value."
            )
            sys.exit(1)
        args.shuffle_seed = args.sample_task_seed

    # Load input
    with open(args.input_json, encoding="utf-8") as f:
        raw_input = json.load(f)

    # Detect input mode
    input_mode = args.input_mode
    if input_mode == "auto":
        if isinstance(raw_input, list):
            input_mode = "records"
        elif isinstance(raw_input, dict) and any(
            k in raw_input for k in ("failure_cause", "failure_memory")
        ):
            input_mode = "patterns"
        else:
            log.error(
                "Cannot auto-detect input format. Use --input-mode to specify."
            )
            sys.exit(1)
    log.info("Input mode: %s", input_mode)

    # Prepare input data
    records = None
    patterns = None

    if input_mode == "records":
        if not isinstance(raw_input, list):
            log.error(
                "Expected a JSON list for records mode, got %s",
                type(raw_input).__name__,
            )
            sys.exit(1)
        records = raw_input
        use_dataset_task_pool = args.data_path is not None

        if args.shuffle_seed is not None and not use_dataset_task_pool:
            rng = random.Random(args.shuffle_seed)
            rng.shuffle(records)
            log.info("Shuffled records with seed %d", args.shuffle_seed)

        if (args.start_idx is not None or args.end_idx is not None) and not use_dataset_task_pool:
            start = args.start_idx or 0
            end = args.end_idx if args.end_idx is not None else len(records)
            records = records[start:end]
            log.info(
                "Loaded %d error records from %s (slice %d:%d)",
                len(records),
                args.input_json,
                start,
                end,
            )
        else:
            log.info(
                "Loaded %d error records from %s", len(records), args.input_json
            )
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
                "Selected %d dataset task ids and kept %d/%d records",
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
                "Sampled %d unique task ids from records and kept %d/%d records",
                len(sampled_task_ids),
                len(records),
                original_record_count,
            )
    else:
        patterns = raw_input
        total_pats = sum(
            len(v) for v in patterns.values() if isinstance(v, list)
        )
        log.info(
            "Loaded %d compressed patterns (%s) from %s",
            total_pats,
            ", ".join(
                f"{k}: {len(v)}" for k, v in patterns.items() if isinstance(v, list)
            ),
            args.input_json,
        )

    # Auto-select prompt variant for patterns mode
    prompt_variant = args.prompt
    if input_mode == "patterns" and prompt_variant in ("skill", "generic"):
        prompt_variant = (
            "patterns" if args.prompt == "skill" else "patterns_generic"
        )
        log.info(
            "Auto-selected --prompt %s (override with --prompt explicitly)",
            prompt_variant,
        )

    # Backup
    backup_path = None
    if args.continue_evolving:
        log.info("Continuing evolution without backup (--continue-evolving)")
    elif not args.dry_run:
        backup_path = backup_skill(args.skill_dir)
        log.info("Backed up skill to %s", backup_path)
    else:
        log.info("[DRY RUN] Skipping backup")

    # Resolve intermediates directory
    intermediates_dir = None
    if args.save_intermediates:
        intermediates_dir = args.intermediates_dir
        if intermediates_dir is None:
            intermediates_dir = (
                args.skill_dir.parent / f"{args.skill_dir.name}_parallel_output"
            )
        intermediates_dir.mkdir(parents=True, exist_ok=True)
        log.info("Intermediate artifacts will be saved to %s", intermediates_dir)

    # Create client
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

    # Create parallel evolver
    evolver = ParallelSkillEvolver(
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
        prompt_variant=prompt_variant,
        output_dir=intermediates_dir,
        parse_failure_dir=args.parse_failure_dir,
        max_skill_lines=args.max_skill_lines,
        skip_translation=args.skip_translation,
        patch_pipeline=args.patch_pipeline,
        semantic_item_marker_format=args.semantic_item_marker_format,
        enable_json_format_self_fix=not args.disable_json_format_self_fix,
    )

    # Require the skill format checker for the apply/validate phase
    if not args.dry_run:
        if not QUICK_VALIDATE_SCRIPT.exists():
            log.error(
                "Skill format checker not found at %s. "
                "The consolidation step requires this script to validate edits.",
                QUICK_VALIDATE_SCRIPT,
            )
            sys.exit(1)

    # Run pipeline
    if input_mode == "records":
        result = evolver.run(records, input_mode="records")
    else:
        result = evolver.run(patterns, input_mode="patterns")

    # Print summary
    print("\n" + "=" * 60)
    print("PARALLEL EVOLUTION SUMMARY")
    print("=" * 60)
    n_patches = len(result.get("patches", []))
    print(f"MAP patches produced:  {n_patches}")
    print(f"LLM calls (est):      {result.get('total_llm_calls', 0)}")
    print(f"Edits applied:         {len(result.get('edits', []))}")
    print(f"Reasoning:             {result.get('reasoning', '')[:200]}")

    changelog = result.get("changelog", [])
    if changelog:
        print("\nChangelog:")
        for entry in changelog:
            print(f"  - {entry}")

    # Print diffs
    diffs = result.get("diffs", [])
    if diffs:
        print("\nDiffs:")
        for d in diffs:
            if d.unified_diff:
                print(f"\n--- {d.relative_path} ({d.action}) ---")
                print(d.unified_diff)
    else:
        print("\nDiffs: (no changes)")

    # Print final vs original diff if backup exists
    if backup_path and not args.dry_run:
        final_diffs = _compute_dir_diff(backup_path, args.skill_dir)
        if final_diffs:
            print("\nFinal vs Original Diff:")
            for diff_text in final_diffs:
                print(diff_text)

    # Optionally write cumulative patch
    cumulative_patch = result.get("cumulative_patch", "")
    if args.patch_file and cumulative_patch:
        args.patch_file.write_text(cumulative_patch, encoding="utf-8")
        log.info("Patch written to %s", args.patch_file)

    # Optionally write changelog
    if args.changelog:
        if backup_path is None:
            diff_blob = "(diff unavailable: no backup)"
        else:
            final_diffs = _compute_dir_diff(backup_path, args.skill_dir)
            diff_blob = "\n".join(final_diffs) if final_diffs else "(no changes)"
        lines: list[str] = []
        lines.append("Change Log (Parallel Evolution):")
        lines.append(f"MAP patches: {n_patches}")
        lines.append(f"LLM calls: {result.get('total_llm_calls', 0)}")
        if changelog:
            lines.append("\nChanges:")
            for entry in changelog:
                lines.append(f"  - {entry}")
        lines.append("")
        lines.append("Overall Diff (final vs original):")
        lines.append("```diff")
        lines.append(diff_blob)
        lines.append("```")
        args.changelog.write_text("\n".join(lines), encoding="utf-8")
        log.info("Changelog written to %s", args.changelog)

    # Optionally copy to output-dir
    if args.output_dir:
        if args.output_dir.exists():
            shutil.rmtree(args.output_dir)
        shutil.copytree(args.skill_dir, args.output_dir)
        log.info("Final skill copied to %s", args.output_dir)

    if intermediates_dir:
        print(f"\nIntermediate artifacts: {intermediates_dir}")


if __name__ == "__main__":
    main()
