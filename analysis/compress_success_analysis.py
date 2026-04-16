#!/usr/bin/env python3
"""
Compress success analysis items into higher-level patterns.

Processes success_memory items, compressing them into reusable pattern summaries.

Usage:
    python analysis/compress_success_analysis.py \
        --input success_items.json \
        --output_dir analysis/success_compressed \
        --model <model_name> \
        --parsed_output success_patterns.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from openai import OpenAI
from tqdm import tqdm

SCRIPT_DIR = Path(__file__).resolve().parent

ITEM_TYPE_CONTEXTS_GENERIC = {
    "success_memory": (
        "Success Memory Items",
        (
            "Each **Success Memory** item describes what the agent did correctly in a task — "
            "an effective behavior or strategy that contributed to success. Items have these fields:\n"
            "- **title**: short name of the success\n"
            "- **description**: one-sentence summary\n"
            "- **content**: detailed explanation (1-3 sentences)\n\n"
            "Focus on the `content` field for understanding what actionable strategy "
            "should be retained and generalized."
        ),
    ),
}


# ---------------------------------------------------------------------------
# Data extraction
# ---------------------------------------------------------------------------


def collect_success_items(records: list[dict]) -> list[dict]:
    """Collect success_memory items from records, attaching instance_id to each."""
    items = []
    for record in records:
        instance_id = record.get("instance_id", "unknown")
        for item in record.get("items", []):
            if item.get("type") == "success_memory":
                items.append({**item, "instance_id": instance_id})
    return items


def group_items_by_task(items: list[dict]) -> list[tuple[str, list[dict]]]:
    """Group items by instance_id, preserving insertion order.

    Returns list of (instance_id, items_for_that_task) tuples.
    """
    from collections import OrderedDict

    groups: OrderedDict[str, list[dict]] = OrderedDict()
    for item in items:
        iid = item.get("instance_id", "unknown")
        groups.setdefault(iid, []).append(item)
    return list(groups.items())


def format_items_for_prompt(
    task_groups: list[tuple[str, list[dict]]],
) -> str:
    """Format items into a text block using simple Task 0, 1, 2... indices.

    The caller maintains the mapping from these indices to real instance IDs.
    """
    blocks: list[str] = []
    for task_idx, (_instance_id, task_items) in enumerate(task_groups):
        lines = [f"Task {task_idx}:"]
        if not task_items:
            lines.append("- (no items)")
        else:
            for item in task_items:
                title = item.get("title", "").strip()
                description = item.get("description", "").strip()
                content = item.get("content", "").strip()

                lines.append(f"- Title: {title}")
                if description:
                    lines.append(f"  Description: {description}")
                if content:
                    lines.append(f"  Content: {content}")
        blocks.append("\n".join(lines))

    return "\n\n".join(blocks)


# ---------------------------------------------------------------------------
# Batching
# ---------------------------------------------------------------------------


def chunk_task_groups(
    task_groups: list[tuple[str, list[dict]]],
    batch_size: int,
) -> list[list[tuple[str, list[dict]]]]:
    """Split task groups into balanced batches of approximately batch_size tasks.

    If the remainder is smaller than half the batch size, distribute its items
    round-robin into the earlier batches.  Otherwise keep the remainder as its
    own batch.
    """
    total = len(task_groups)
    if total <= batch_size:
        return [list(task_groups)]

    n_full = total // batch_size
    remainder = total % batch_size

    if remainder == 0:
        return [
            list(task_groups[i : i + batch_size])
            for i in range(0, total, batch_size)
        ]

    # Build full batches
    batches = [
        list(task_groups[i : i + batch_size])
        for i in range(0, n_full * batch_size, batch_size)
    ]

    if remainder < batch_size / 2:
        # Small remainder — distribute round-robin into earlier batches
        for i, item in enumerate(task_groups[n_full * batch_size :]):
            batches[i % len(batches)].append(item)
    else:
        # Large enough remainder — keep as its own batch
        batches.append(list(task_groups[n_full * batch_size :]))

    return batches


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------


def load_prompts() -> tuple[str, str]:
    """Load system and user prompt templates."""
    system_template = (SCRIPT_DIR / "success_compression.txt").read_text()
    user_template = (SCRIPT_DIR / "success_compression_user.txt").read_text()
    return system_template, user_template


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


def build_messages(
    system_template: str,
    user_template: str,
    task_groups: list[tuple[str, list[dict]]],
) -> tuple[str, str]:
    """Build system and user messages for a compression call."""
    label, context = ITEM_TYPE_CONTEXTS_GENERIC["success_memory"]
    system_msg = system_template.format(
        item_type_label=label,
        item_type_context=context,
    )
    extracted = format_items_for_prompt(task_groups)
    user_msg = user_template.format(
        item_type_label=label,
        extracted_items=extracted,
    )
    return system_msg, user_msg


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------


def compress_batch(
    client: OpenAI,
    model: str,
    system_msg: str,
    user_msg: str,
    generation_config: dict | None = None,
) -> str:
    """Send compression request to LLM and return raw response text."""
    request_kwargs = dict(generation_config or {})
    request_kwargs.pop("model", None)
    request_kwargs.pop("messages", None)

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        **request_kwargs,
    )
    return response.choices[0].message.content


# ---------------------------------------------------------------------------
# Output parsing
# ---------------------------------------------------------------------------

_PATTERN_RE = re.compile(
    r"^#\s+Pattern\s+(\d+)[^\n]*\n(.*?)(?=\n#\s+Pattern\s+\d+|\Z)",
    re.MULTILINE | re.DOTALL,
)

_SECTION_RE = re.compile(
    r"^##\s+{name}\s*\n(.*?)(?=\n##\s+|\Z)",
    re.MULTILINE | re.DOTALL,
)


def _extract_section(body: str, name: str) -> str:
    pat = _SECTION_RE.pattern.format(name=re.escape(name))
    m = re.search(pat, body, re.MULTILINE | re.DOTALL)
    return m.group(1).strip() if m else ""


def _strip_think(text: str) -> str:
    if "</think>" in text:
        return text.rsplit("</think>", 1)[-1]
    return text


def parse_compression_output(text: str) -> list[dict]:
    """Parse Pattern blocks from LLM response text."""
    text = _strip_think(text)
    # Strip outer code fence if the entire response is wrapped in one
    stripped = text.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        stripped = re.sub(r"^```\w*\n?", "", stripped)
        stripped = re.sub(r"\n?```$", "", stripped)
        text = stripped
    # Remove any remaining code fence lines (handles per-pattern fences or
    # cases where the LLM wraps individual patterns in ```markdown ... ```)
    text = re.sub(r"^```\w*\s*$", "", text, flags=re.MULTILINE)

    patterns = []
    for match in _PATTERN_RE.finditer(text):
        index = int(match.group(1))
        body = match.group(2).strip()
        source_cases = _extract_section(body, "Source Cases")
        # Extract integer indices from the source cases text
        case_indices: list[int] = []
        for token in re.findall(r"\b\d+\b", source_cases):
            try:
                case_indices.append(int(token))
            except ValueError:
                continue
        patterns.append({
            "index": index,
            "title": _extract_section(body, "Title"),
            "description": _extract_section(body, "Description"),
            "frequency": _extract_section(body, "Frequency"),
            "source_cases": source_cases,
            "source_case_indices": case_indices,
            "covered_specific_successes": _extract_section(body, "Covered Specific Successes"),
        })
    return patterns


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run_compression(
    client: OpenAI,
    model: str,
    system_template: str,
    user_template: str,
    items: list[dict],
    batch_size: int,
    output_dir: Path,
    max_workers: int,
    generation_config: dict | None = None,
) -> list[dict]:
    """Run compression for all success_memory items. Returns parsed patterns."""
    label, _ = ITEM_TYPE_CONTEXTS_GENERIC["success_memory"]
    type_dir = output_dir / "success_memory"
    type_dir.mkdir(parents=True, exist_ok=True)

    # Group items by task, then batch the task groups
    task_groups = group_items_by_task(items)
    batches = chunk_task_groups(task_groups, batch_size)
    total_items = len(items)

    print(f"\n{'='*60}")
    print(
        f"Compressing {label}: {total_items} items across "
        f"{len(task_groups)} tasks in {len(batches)} batch(es)"
    )
    print(f"{'='*60}")

    all_parsed: list[dict] = []

    def run_batch(
        batch_idx: int, batch_groups: list[tuple[str, list[dict]]]
    ) -> tuple[int, str, list[dict], list[str]]:
        sys_msg, usr_msg = build_messages(system_template, user_template, batch_groups)
        raw = compress_batch(client, model, sys_msg, usr_msg, generation_config)

        # id_map: task index -> real instance_id
        id_map = [iid for iid, _ in batch_groups]

        # Save raw output
        raw_path = type_dir / f"compression_{batch_idx:04d}.md"
        raw_path.write_text(raw, encoding="utf-8")

        # Save id map
        map_path = type_dir / f"compression_{batch_idx:04d}.map.json"
        map_path.write_text(json.dumps(id_map, indent=2), encoding="utf-8")

        # Parse and resolve indices to real task IDs
        parsed = parse_compression_output(raw)
        for pat in parsed:
            indices = pat.get("source_case_indices", [])
            pat["source_case_task_ids"] = [
                id_map[i] for i in indices if 0 <= i < len(id_map)
            ]

        return batch_idx, raw, parsed, id_map

    # Print first prompt for debugging (without running it separately)
    if batches:
        sys_msg, usr_msg = build_messages(system_template, user_template, batches[0])
        print(f"\n--- Debug: First Prompt (System) ---\n")
        print(sys_msg)
        print(f"\n--- Debug: First Prompt (User) ---\n")
        print(usr_msg)

    if batches:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(run_batch, idx, batch): idx
                for idx, batch in enumerate(batches)
            }
            with tqdm(
                total=len(futures), unit="batch", desc=f"Compressing {label}"
            ) as pbar:
                for future in as_completed(futures):
                    idx = futures[future]
                    try:
                        _, _, parsed, _ = future.result()
                        all_parsed.extend(parsed)
                        tqdm.write(f"  Batch {idx}: {len(parsed)} patterns")
                    except Exception as e:
                        tqdm.write(f"  Batch {idx}: ERROR: {e}")
                    finally:
                        pbar.update(1)

    return all_parsed


def main():
    parser = argparse.ArgumentParser(
        description="Compress success analysis items into patterns",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--input",
        type=str,
        required=True,
        help="JSON file from parse_success_analysis_outputs.py",
    )
    parser.add_argument(
        "--output_dir",
        type=str,
        required=True,
        help="Directory to save compressed outputs (success_memory subdir)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="LLM model name (default: OPENAI_MODEL env var)",
    )
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
    parser.add_argument(
        "--batch_size",
        type=int,
        default=0,
        help="Tasks per LLM call (0 = all tasks in one call)",
    )
    parser.add_argument(
        "--max_workers",
        type=int,
        default=4,
        help="Max parallel LLM calls",
    )
    parser.add_argument(
        "--parsed_output",
        type=str,
        default=None,
        help="Save parsed patterns as JSON to this path",
    )
    args = parser.parse_args()
    generation_config = build_generation_config(args)

    model = args.model or os.getenv("OPENAI_MODEL")
    if not model:
        print(
            "Error: model must be specified via --model or OPENAI_MODEL env var",
            file=sys.stderr,
        )
        sys.exit(1)

    base_url = os.getenv("OPENAI_BASE_URL", "http://localhost:8000/v1")
    api_key = os.getenv("OPENAI_API_KEY", "EMPTY")
    client = OpenAI(api_key=api_key, base_url=base_url)

    system_template, user_template = load_prompts()

    records = json.loads(Path(args.input).read_text())
    if not records:
        print("No records found in input.", file=sys.stderr)
        sys.exit(1)

    items = collect_success_items(records)
    if not items:
        print("No success_memory items found in input.", file=sys.stderr)
        sys.exit(1)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    task_count = len(group_items_by_task(items))
    batch_size = args.batch_size if args.batch_size > 0 else task_count

    patterns = run_compression(
        client=client,
        model=model,
        system_template=system_template,
        user_template=user_template,
        items=items,
        batch_size=batch_size,
        output_dir=output_dir,
        max_workers=args.max_workers,
        generation_config=generation_config,
    )

    # Summary
    print(f"\n{'='*60}")
    print("COMPRESSION SUMMARY")
    print(f"{'='*60}")
    print(f"  Success Memory Items: {len(items)} items -> {len(patterns)} patterns")
    print(f"  Output directory: {output_dir}")

    # Save parsed output
    if args.parsed_output:
        result = {"success_memory": patterns}
        Path(args.parsed_output).write_text(
            json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"  Parsed patterns saved to: {args.parsed_output}")


if __name__ == "__main__":
    main()
