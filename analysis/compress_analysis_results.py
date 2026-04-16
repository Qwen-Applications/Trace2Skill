#!/usr/bin/env python3
"""
Compress parsed analysis items into higher-level patterns using an LLM.

This script consumes JSON output from parse_analysis_results.py and batches
items by task (instance_id). Each LLM call must include items from exactly
20 tasks.

Usage:
    python analysis/compress_analysis_results.py \
        --input analysis/results.json \
        --output_dir analysis/compressed \
        --item_name "Behavior Change Items"
"""

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from openai import OpenAI
from tqdm import tqdm

import parse_compression_results as compression_parser

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_BATCH_SIZE = 20


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


def load_prompt_files() -> tuple[str, str]:
    system_prompt = (SCRIPT_DIR / "compression.txt").read_text()
    user_template = (SCRIPT_DIR / "compression_user.txt").read_text()
    return system_prompt, user_template


def format_items(records: list[dict]) -> str:
    blocks = []
    for idx, record in enumerate(records):
        items = record.get("items", [])
        lines = [f"Task {idx}:"]
        if not items:
            lines.append("- (no items)")
        else:
            for item in items:
                item_type = item.get("type", "unknown")
                title = item.get("title", "").strip()
                description = item.get("description", "").strip()
                content = item.get("content", "").strip()
                lines.append(f"- [{item_type}] Title: {title}")
                if description:
                    lines.append(f"  Description: {description}")
                if content:
                    lines.append(f"  Content: {content}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def chunk_records(records: list[dict], batch_size: int) -> list[list[dict]]:
    total = len(records)
    if total % batch_size != 0:
        raise ValueError(
            f"Total tasks ({total}) is not divisible by {batch_size}. "
            f"Every call must include exactly {batch_size} tasks."
        )
    return [records[i:i + batch_size] for i in range(0, total, batch_size)]


def compress_batch(
    client: OpenAI,
    model: str,
    system_prompt: str,
    user_template: str,
    item_name: str,
    records: list[dict],
    generation_config: dict | None = None,
) -> tuple[str, str, str, list[str]]:
    id_map = [r.get("instance_id", "unknown") for r in records]
    extracted_items = format_items(records)
    system_message = system_prompt.format(item_name=item_name)
    user_message = user_template.format(extracted_items=extracted_items)

    request_kwargs = dict(generation_config or {})
    request_kwargs.pop("model", None)
    request_kwargs.pop("messages", None)

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_message},
            {"role": "user", "content": user_message},
        ],
        **request_kwargs,
    )
    return response.choices[0].message.content, system_message, user_message, id_map


def main():
    parser = argparse.ArgumentParser(
        description="Compress analysis items into higher-level patterns using an LLM"
    )
    parser.add_argument(
        "--input",
        type=str,
        required=True,
        help="JSON file produced by parse_analysis_results.py",
    )
    parser.add_argument(
        "--output_dir",
        type=str,
        required=True,
        help="Directory to save compressed outputs",
    )
    parser.add_argument(
        "--item_name",
        type=str,
        default="items",
        help="Name to inject into compression prompt (e.g., 'Behavior Change Items')",
    )
    parser.add_argument(
        "--batch_size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help="Number of tasks per LLM call (default: 20)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="vLLM model name (default: OPENAI_MODEL env var)",
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
        "--parsed_output",
        type=str,
        default=None,
        help="Optional JSON path to save parsed compression outputs",
    )
    parser.add_argument(
        "--max_workers",
        type=int,
        default=None,
        help="Max threads for parallel API calls (default: min(32, cpu*4))",
    )
    args = parser.parse_args()
    generation_config = build_generation_config(args)

    model = args.model or os.getenv("OPENAI_MODEL")
    if not model:
        print("Error: model must be specified via --model or OPENAI_MODEL env var", file=sys.stderr)
        sys.exit(1)

    base_url = os.getenv("OPENAI_BASE_URL", "http://localhost:8000/v1")
    api_key = os.getenv("OPENAI_API_KEY", "EMPTY")
    client = OpenAI(api_key=api_key, base_url=base_url)

    system_prompt, user_template = load_prompt_files()

    records = json.loads(Path(args.input).read_text())
    if not records:
        print("No records found in input.", file=sys.stderr)
        sys.exit(1)

    if args.batch_size <= 0:
        print("Error: --batch_size must be positive.", file=sys.stderr)
        sys.exit(1)

    batches = chunk_records(records, args.batch_size)
    os.makedirs(args.output_dir, exist_ok=True)

    max_workers = args.max_workers or min(32, (os.cpu_count() or 1) * 4)

    def run_batch(batch_index: int, batch_records: list[dict]) -> str:
        output_path = os.path.join(args.output_dir, f"compression_{batch_index:04d}.md")
        result, _, _, id_map = compress_batch(
            client, model, system_prompt, user_template, args.item_name, batch_records, generation_config
        )
        Path(output_path).write_text(result)
        map_path = os.path.join(args.output_dir, f"compression_{batch_index:04d}.map.json")
        Path(map_path).write_text(json.dumps(id_map, indent=2))
        return output_path

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        if batches:
            first_result, first_system, first_user, first_map = compress_batch(
                client, model, system_prompt, user_template, args.item_name, batches[0], generation_config
            )
            first_output_path = os.path.join(args.output_dir, "compression_0001.md")
            Path(first_output_path).write_text(first_result)
            first_map_path = os.path.join(args.output_dir, "compression_0001.map.json")
            Path(first_map_path).write_text(json.dumps(first_map, indent=2))
            print("\n--- Debug: First Prompt (System) ---\n")
            print(first_system)
            print("\n--- Debug: First Prompt (User) ---\n")
            print(first_user)
            remaining_batches = batches[1:]
            start_index = 2
        else:
            remaining_batches = []
            start_index = 1

        futures = {
            executor.submit(run_batch, idx, batch): idx
            for idx, batch in enumerate(remaining_batches, start_index)
        }
        with tqdm(total=len(futures), unit="batch", desc="Compressing") as pbar:
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    output_path = future.result()
                    tqdm.write(f"Batch {idx}: done -> {output_path}")
                except Exception as e:
                    tqdm.write(f"Batch {idx}: ERROR: {e}")
                finally:
                    pbar.update(1)

    print(f"Wrote {len(batches)} compressed files to {args.output_dir}")

    if args.parsed_output:
        md_files = sorted(
            os.path.join(args.output_dir, f)
            for f in os.listdir(args.output_dir)
            if f.endswith(".md")
        )
        results = []
        for filepath in md_files:
            record = compression_parser.parse_file(filepath)
            if record["patterns"]:
                results.append(record)
        Path(args.parsed_output).write_text(json.dumps(results, indent=2))
        total_patterns = sum(len(r["patterns"]) for r in results)
        print(f"Wrote {len(results)} records ({total_patterns} patterns) to {args.parsed_output}")


if __name__ == "__main__":
    main()
