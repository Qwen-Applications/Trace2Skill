#!/usr/bin/env python3
"""
Meta-compress pattern summaries into higher-level meta-patterns using an LLM.

Consumes JSON output from parse_compression_results.py.
"""

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
    system_prompt = (SCRIPT_DIR / "meta_compression.txt").read_text()
    user_template = (SCRIPT_DIR / "meta_compression_user.txt").read_text()
    return system_prompt, user_template


def format_patterns(records: list[dict]) -> str:
    blocks = []
    for record in records:
        batch_id = record.get("batch_id", "unknown")
        patterns = record.get("patterns", [])
        lines = [f"Batch {batch_id}:"]
        if not patterns:
            lines.append("- (no patterns)")
        else:
            for pat in patterns:
                index = pat.get("index", "unknown")
                title = pat.get("title", "").strip()
                description = pat.get("description", "").strip()
                frequency = pat.get("frequency", "").strip()
                source_cases = pat.get("source_cases", "").strip()
                covered_items = pat.get("covered_items", "").strip()
                lines.append(f"- Pattern {index}: {title}")
                if description:
                    lines.append(f"  Description: {description}")
                if frequency:
                    lines.append(f"  Frequency: {frequency}")
                if source_cases:
                    lines.append(f"  Source Cases: {source_cases}")
                if covered_items:
                    lines.append(f"  Covered Items: {covered_items}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


META_ITEM_PATTERN = re.compile(
    r"^#\s+Meta-Pattern\s+(\d+)\s*\n"
    r"(.*?)(?=\n#\s+Meta-Pattern\s+\d+|\Z)",
    re.MULTILINE | re.DOTALL,
)

META_SECTION_PATTERN = re.compile(
    r"^##\s+{name}\s*\n(.*?)(?=\n##\s+|\Z)",
    re.MULTILINE | re.DOTALL,
)


def strip_code_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        stripped = re.sub(r"^```\w*\n?", "", stripped)
        stripped = re.sub(r"\n?```$", "", stripped)
    return stripped


def strip_think_prefix(text: str) -> str:
    if "</think>" not in text:
        return text
    return text.rsplit("</think>", 1)[-1]


def extract_section(body: str, section_name: str) -> str:
    pattern = META_SECTION_PATTERN.pattern.format(name=re.escape(section_name))
    match = re.search(pattern, body, re.MULTILINE | re.DOTALL)
    return match.group(1).strip() if match else ""


def parse_meta_patterns(text: str) -> list[dict]:
    text = strip_think_prefix(text)
    text = strip_code_fences(text)
    patterns = []

    for match in META_ITEM_PATTERN.finditer(text):
        index = int(match.group(1))
        body = match.group(2).strip()
        patterns.append({
            "index": index,
            "title": extract_section(body, "Title"),
            "description": extract_section(body, "Description"),
            "covered_patterns": extract_section(body, "Covered Patterns"),
            "mechanism": extract_section(body, "Mechanism"),
        })

    return patterns


def meta_compress(
    client: OpenAI,
    model: str,
    system_prompt: str,
    user_template: str,
    records: list[dict],
    generation_config: dict | None = None,
) -> tuple[str, str, str]:
    patterns = format_patterns(records)
    user_message = user_template.format(patterns=patterns)
    request_kwargs = dict(generation_config or {})
    request_kwargs.pop("model", None)
    request_kwargs.pop("messages", None)

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        **request_kwargs,
    )
    return response.choices[0].message.content, system_prompt, user_message


def chunk_records(records: list[dict], batch_size: int) -> list[list[dict]]:
    return [records[i:i + batch_size] for i in range(0, len(records), batch_size)]


def main():
    parser = argparse.ArgumentParser(
        description="Meta-compress pattern summaries into higher-level meta-patterns"
    )
    parser.add_argument(
        "--input",
        type=str,
        required=True,
        help="JSON file(s) produced by parse_compression_results.py (comma-separated) or a directory",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output markdown file path (used only when a single batch is produced)",
    )
    parser.add_argument(
        "--output_dir",
        type=str,
        default=None,
        help="Directory to save per-batch meta-compression outputs",
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
        help="Optional JSON path to save parsed meta-compression outputs",
    )
    parser.add_argument(
        "--batch_size",
        type=int,
        default=None,
        help="Number of pattern batches per LLM call (default: all in one batch)",
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

    if os.path.isdir(args.input):
        input_paths = sorted(
            str(p) for p in Path(args.input).iterdir() if p.suffix == ".json"
        )
    else:
        input_paths = [p.strip() for p in args.input.split(",") if p.strip()]
    if not input_paths:
        print("Error: --input must include at least one JSON file.", file=sys.stderr)
        sys.exit(1)

    records = []
    for path in input_paths:
        chunk = json.loads(Path(path).read_text())
        if isinstance(chunk, list):
            records.extend(chunk)
        else:
            print(f"Warning: {path} did not contain a list, skipping.", file=sys.stderr)
    if not records:
        print("No records found in input.", file=sys.stderr)
        sys.exit(1)

    if args.batch_size is not None and args.batch_size <= 0:
        print("Error: --batch_size must be positive.", file=sys.stderr)
        sys.exit(1)

    batches = (
        chunk_records(records, args.batch_size)
        if args.batch_size
        else [records]
    )

    if len(batches) == 1:
        output_path = args.output or args.output_dir
        if not output_path:
            print("Error: provide --output (or --output_dir) for single-batch output.", file=sys.stderr)
            sys.exit(1)
        result, _, _ = meta_compress(
            client, model, system_prompt, user_template, batches[0], generation_config
        )
        Path(output_path).write_text(result)
        print(f"Wrote meta-compression output to {output_path}")
        if args.parsed_output:
            patterns = parse_meta_patterns(result)
            Path(args.parsed_output).write_text(json.dumps(patterns, indent=2))
            print(f"Wrote {len(patterns)} meta-patterns to {args.parsed_output}")
        return

    if not args.output_dir:
        print("Error: --output_dir is required when multiple batches are produced.", file=sys.stderr)
        sys.exit(1)

    os.makedirs(args.output_dir, exist_ok=True)
    max_workers = args.max_workers or min(32, (os.cpu_count() or 1) * 4)

    def run_batch(batch_index: int, batch_records: list[dict]) -> str:
        output_path = os.path.join(args.output_dir, f"meta_compression_{batch_index:04d}.md")
        result, _, _ = meta_compress(
            client, model, system_prompt, user_template, batch_records, generation_config
        )
        Path(output_path).write_text(result)
        return output_path

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        if batches:
            first_result, first_system, first_user = meta_compress(
                client, model, system_prompt, user_template, batches[0], generation_config
            )
            first_output_path = os.path.join(args.output_dir, "meta_compression_0001.md")
            Path(first_output_path).write_text(first_result)
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
        with tqdm(total=len(futures), unit="batch", desc="Meta-Compressing") as pbar:
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    output_path = future.result()
                    tqdm.write(f"Batch {idx}: done -> {output_path}")
                except Exception as e:
                    tqdm.write(f"Batch {idx}: ERROR: {e}")
                finally:
                    pbar.update(1)

    print(f"Wrote {len(batches)} meta-compression files to {args.output_dir}")
    if args.parsed_output:
        md_files = sorted(
            os.path.join(args.output_dir, f)
            for f in os.listdir(args.output_dir)
            if f.endswith(".md")
        )
        parsed_records = []
        for filepath in md_files:
            text = Path(filepath).read_text()
            patterns = parse_meta_patterns(text)
            if patterns:
                parsed_records.append({
                    "source_file": os.path.basename(filepath),
                    "patterns": patterns,
                })
        Path(args.parsed_output).write_text(json.dumps(parsed_records, indent=2))
        total_patterns = sum(len(r["patterns"]) for r in parsed_records)
        print(f"Wrote {len(parsed_records)} records ({total_patterns} patterns) to {args.parsed_output}")


if __name__ == "__main__":
    main()
