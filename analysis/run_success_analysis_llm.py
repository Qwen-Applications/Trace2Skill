#!/usr/bin/env python3
"""
LLM-only success analysis pipeline for successful spreadsheet agent runs.

For each SUCCEED trajectory, makes a single LLM call that:
  1. Distills the lean solution path (failed attempts removed)
  2. Extracts generalizable Success Memory Items

Follows the same MapReduce structure as run_error_analysis_llm.py:

  Map  : one LLM call per instance  (parallel via ThreadPoolExecutor)
  Write: per-instance Markdown report saved to --output_dir

Usage:
    python analysis/run_success_analysis_llm.py \\
        --logs_dir agent_output/cli_only_logs \\
        --output_dir analysis/llm_success_analysis \\
        --model <model-name>

    # Specific instances
    python analysis/run_success_analysis_llm.py \\
        --logs_dir agent_output/cli_only_logs \\
        --output_dir analysis/llm_success_analysis \\
        --model <model-name> --instance_ids 10452,10747

    # Include all outcomes, not just SUCCEED
    python analysis/run_success_analysis_llm.py \\
        --logs_dir agent_output/cli_only_logs \\
        --output_dir analysis/llm_success_analysis \\
        --model <model-name> --all

Environment variables:
    OPENAI_BASE_URL  - API endpoint (default: http://localhost:8000/v1)
    OPENAI_API_KEY   - API key (default: EMPTY for local vLLM)
    OPENAI_MODEL     - Model name (required if --model not given)
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
SYSTEM_PROMPT_PATH = SCRIPT_DIR / "success_analysis_system_llm.txt"
USER_PROMPT_PATH = SCRIPT_DIR / "success_analysis_user_llm.txt"


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def parse_generation_config(value: str | None) -> dict:
    """Parse generation config from a JSON string or a path to a JSON file."""
    if not value:
        return {}
    if os.path.isfile(value):
        with open(value, "r", encoding="utf-8") as fh:
            parsed = json.load(fh)
    else:
        parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("--generation_config must be a JSON object or path to one")
    return parsed


def build_generation_config(args) -> dict:
    cfg = parse_generation_config(args.generation_config)
    if args.seed is not None:
        cfg["seed"] = args.seed
    return cfg


# ---------------------------------------------------------------------------
# Log discovery
# ---------------------------------------------------------------------------

def parse_log_filename(name: str) -> tuple[str, str] | None:
    """
    Extract (instance_id, outcome) from a log filename.

    Expected pattern: <any_prefix><instance_id>_<SUCCEED|FAILED>.md
    The instance_id is the last underscore-separated component before the outcome tag.
    Examples:
        cli_only_agent_10747_SUCCEED.md  -> ("10747", "SUCCEED")
        cli_skill_agent_105-24_FAILED.md -> ("105-24", "FAILED")
    """
    if not name.endswith(".md"):
        return None
    stem = name[: -len(".md")]
    match = re.match(r"^(.+)_(SUCCEED|FAILED)$", stem)
    if not match:
        return None
    parts = match.group(1).split("_")
    return parts[-1], match.group(2)


def find_logs(
    logs_dir: str,
    succeed_only: bool = True,
) -> dict[str, str]:
    """
    Return a mapping of instance_id -> log_path for logs in logs_dir.

    When succeed_only=True (default), only SUCCEED logs are included.
    """
    result = {}
    for name in sorted(os.listdir(logs_dir)):
        parsed = parse_log_filename(name)
        if parsed is None:
            continue
        instance_id, outcome = parsed
        if succeed_only and outcome != "SUCCEED":
            continue
        result[instance_id] = os.path.join(logs_dir, name)
    return result


def find_logs_repeat(
    logs_dir: str,
    succeed_only: bool = True,
) -> dict[str, str]:
    """
    Return a mapping of composite_id -> log_path for all seed_* subdirectories.

    composite_id = "{instance_id}_{seed_name}" (e.g. "10452_seed_42").
    When succeed_only=True (default), only SUCCEED logs are included.
    """
    result = {}
    for entry in sorted(os.scandir(logs_dir)):
        if not (entry.is_dir() and entry.name.startswith("seed_")):
            continue
        seed_name = entry.name
        for name in sorted(os.listdir(entry.path)):
            parsed = parse_log_filename(name)
            if parsed is None:
                continue
            instance_id, outcome = parsed
            if succeed_only and outcome != "SUCCEED":
                continue
            composite_id = f"{instance_id}_{seed_name}"
            result[composite_id] = os.path.join(entry.path, name)
    return result


# ---------------------------------------------------------------------------
# Log preprocessing
# ---------------------------------------------------------------------------

def strip_log_metadata(text: str) -> str:
    """Remove markdown logger header/trailer sections from the agent log."""
    # Strip header: "# Chat History ...\n\n**Timestamp**: ...\n\n---\n\n"
    text = re.sub(
        r"\A# Chat History[^\n]*\n\n\*\*Timestamp\*\*:[^\n]*\n\n---\n\n",
        "",
        text,
        flags=re.DOTALL,
    )
    # Strip trailing RESULT section
    text = re.sub(r"\n---\n\n## RESULT\n.*\Z", "", text, flags=re.DOTALL)
    return text.strip()


def write_prompt_log(
    log_path: Path,
    input_messages: list[dict[str, str]],
    output_message: str,
) -> None:
    sections = ["# Input", ""]
    for message in input_messages:
        sections.extend([f"## {message['role'].title()}", message["content"], ""])
    sections.extend(["# Output", "", output_message, ""])
    log_path.write_text("\n".join(sections), encoding="utf-8")


# ---------------------------------------------------------------------------
# Single-instance analysis
# ---------------------------------------------------------------------------

def analyze_instance(
    client: OpenAI,
    model: str,
    system_prompt: str,
    user_template: str,
    log_path: str,
    generation_config: dict | None = None,
) -> tuple[list[dict[str, str]], str]:
    """Make a single LLM call to analyze one successful agent log."""
    raw_log = Path(log_path).read_text(encoding="utf-8", errors="replace")
    agent_log = strip_log_metadata(raw_log)

    user_message = user_template.replace("{agent_log}", agent_log)
    input_messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    request_kwargs = dict(generation_config or {})
    request_kwargs.pop("model", None)
    request_kwargs.pop("messages", None)

    response = client.chat.completions.create(
        model=model,
        messages=input_messages,
        **request_kwargs,
    )
    return input_messages, response.choices[0].message.content


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="LLM-only success analysis for successful spreadsheet agent runs."
    )
    parser.add_argument(
        "--logs_dir",
        required=True,
        help="Directory containing agent trajectory log files",
    )
    parser.add_argument(
        "--output_dir",
        required=True,
        help="Directory to save per-instance analysis reports",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="LLM model name (default: OPENAI_MODEL env var)",
    )
    parser.add_argument(
        "--base_url",
        default=None,
        help="OpenAI-compatible API base URL (default: OPENAI_BASE_URL env var or http://localhost:8000/v1)",
    )
    parser.add_argument(
        "--api_key",
        default=None,
        help="API key (default: OPENAI_API_KEY env var or EMPTY)",
    )
    parser.add_argument(
        "--generation_config",
        default=None,
        help="Generation config as a JSON string or path to a JSON file",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Seed merged into generation config",
    )
    parser.add_argument(
        "--instance_ids",
        default=None,
        help="Comma-separated instance IDs to analyze (default: all discovered)",
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=None,
        help="Only analyze the first N instances",
    )
    parser.add_argument(
        "--all",
        dest="all_outcomes",
        action="store_true",
        help="Include all log outcomes, not just SUCCEED (default: SUCCEED only)",
    )
    parser.add_argument(
        "--max_workers",
        type=int,
        default=None,
        help="Max parallel LLM calls (default: min(32, cpu_count * 4))",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="Number of seed runs. When > 1, discovers logs from seed_*/ subdirectories "
             "under --logs_dir and uses composite IDs like {id}_seed_{seed} (default: 1).",
    )

    args = parser.parse_args()
    generation_config = build_generation_config(args)

    # Resolve model
    model = args.model or os.getenv("OPENAI_MODEL")
    if not model:
        print("Error: model must be specified via --model or OPENAI_MODEL env var", file=sys.stderr)
        sys.exit(1)

    base_url = args.base_url or os.getenv("OPENAI_BASE_URL", "http://localhost:8000/v1")
    api_key = args.api_key or os.getenv("OPENAI_API_KEY", "EMPTY")
    client = OpenAI(api_key=api_key, base_url=base_url)

    system_prompt = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    user_template = USER_PROMPT_PATH.read_text(encoding="utf-8")

    # Discover logs
    if args.repeat > 1:
        all_logs = find_logs_repeat(
            args.logs_dir,
            succeed_only=not args.all_outcomes,
        )
    else:
        all_logs = find_logs(
            args.logs_dir,
            succeed_only=not args.all_outcomes,
        )

    # Apply --instance_ids filter
    if args.instance_ids:
        requested = {s.strip() for s in args.instance_ids.split(",")}
        missing = requested - set(all_logs)
        if missing:
            print(f"Warning: instance IDs not found: {sorted(missing)}", file=sys.stderr)
        all_logs = {iid: path for iid, path in all_logs.items() if iid in requested}

    if not all_logs:
        print("No log files found to analyze.", file=sys.stderr)
        sys.exit(1)

    # Apply --sample
    if args.sample is not None:
        all_logs = dict(list(all_logs.items())[: args.sample])

    os.makedirs(args.output_dir, exist_ok=True)

    # Separate already-done from pending
    tasks = []
    skipped = 0
    for iid, log_path in all_logs.items():
        out_path = os.path.join(args.output_dir, f"success_analysis_{iid}.md")
        if os.path.isfile(out_path):
            skipped += 1
        else:
            tasks.append((iid, log_path, out_path))

    print(f"Logs discovered:  {len(all_logs)}")
    print(f"Model:            {model}")
    print(f"Endpoint:         {base_url}")
    print(f"Output dir:       {args.output_dir}")
    if skipped:
        print(f"Skipped (exists): {skipped}")
    print(f"To analyze:       {len(tasks)}")
    print("-" * 60)

    if not tasks:
        print("Nothing to do.")
        return

    max_workers = args.max_workers or min(32, (os.cpu_count() or 1) * 4)

    def run_one(iid: str, log_path: str, out_path: str) -> str:
        prompt_path = Path(args.output_dir) / f"success_analysis_{iid}_prompt.md"
        input_messages, report = analyze_instance(
            client, model, system_prompt, user_template, log_path, generation_config
        )
        Path(out_path).write_text(report, encoding="utf-8")
        write_prompt_log(prompt_path, input_messages, report)
        return out_path

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(run_one, iid, log_path, out_path): iid
            for iid, log_path, out_path in tasks
        }
        with tqdm(total=len(futures), unit="instance", desc="Analyzing") as pbar:
            for future in as_completed(futures):
                iid = futures[future]
                try:
                    out_path = future.result()
                    tqdm.write(f"{iid}: done -> {out_path}")
                except Exception as exc:
                    tqdm.write(f"{iid}: ERROR: {exc}")
                finally:
                    pbar.update(1)


if __name__ == "__main__":
    main()
