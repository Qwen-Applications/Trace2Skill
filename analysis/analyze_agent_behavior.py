#!/usr/bin/env python3
"""
Analyze behavior differences between raw and skill-augmented agents using vLLM.

Supports four analysis modes via --prompt_type:
    reflection  - General behavior differences (all pairs)
    success     - Why the skill caused a previously failing agent to succeed
                  (raw=FAILED, skill=SUCCEED pairs only)
    failure     - Why the skill caused a previously succeeding agent to fail
                  (raw=SUCCEED, skill=FAILED pairs only)
    utility     - Which parts of the skill file are exercised by the agent
                  (skill logs only — no --raw_log_dir needed)

Usage:
    # General behavior analysis on all pairs
    python analysis/analyze_agent_behavior.py \
        --raw_log_dir agent_output/cli_only_logs \
        --skill_log_dir agent_output/cli_skill_logs \
        --output_dir analysis/results \
        --prompt_type reflection

    # Analyze only skill-caused successes
    python analysis/analyze_agent_behavior.py \
        --raw_log_dir agent_output/cli_only_logs \
        --skill_log_dir agent_output/cli_skill_logs \
        --output_dir analysis/results \
        --prompt_type success

    # Skill utility analysis (single trajectory, no raw logs)
    python analysis/analyze_agent_behavior.py \
        --skill_log_dir agent_output/cli_skill_logs \
        --output_dir analysis/results \
        --prompt_type utility

    # Single instance
    python analysis/analyze_agent_behavior.py \
        --raw_log_dir agent_output/cli_only_logs \
        --skill_log_dir agent_output/cli_skill_logs \
        --output_dir analysis/results \
        --prompt_type failure --instance_id 10452

Environment variables:
    OPENAI_BASE_URL  - vLLM endpoint (default: http://localhost:8000/v1)
    OPENAI_API_KEY   - API key (default: EMPTY for local vLLM)
    OPENAI_MODEL     - Model name (required, or pass --model)
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
from transformers import AutoTokenizer


SCRIPT_DIR = Path(__file__).resolve().parent

# Maps each prompt_type to its prompt files, output prefix, and required outcome filters.
# raw_outcome / skill_outcome of None means no filter on that side.
PROMPT_CONFIGS = {
    "reflection": {
        "system_file": "skill_usage_reflection.txt",
        "user_file": "skill_usage_reflection_user.txt",
        "output_prefix": "behavior_analysis",
        "single_trajectory": False,
        "raw_outcome": None,
        "skill_outcome": None,
    },
    "success": {
        "system_file": "skill_caused_success.txt",
        "user_file": "skill_caused_success_user.txt",
        "output_prefix": "success_cause",
        "single_trajectory": False,
        "raw_outcome": "FAILED",
        "skill_outcome": "SUCCEED",
    },
    "failure": {
        "system_file": "skill_caused_failure.txt",
        "user_file": "skill_caused_failure_user.txt",
        "output_prefix": "failure_cause",
        "single_trajectory": False,
        "raw_outcome": "SUCCEED",
        "skill_outcome": "FAILED",
    },
    "utility": {
        "system_file": "skill_utility.txt",
        "user_file": "skill_utility_user.txt",
        "output_prefix": "skill_utility",
        "single_trajectory": True,
        "raw_outcome": None,
        "skill_outcome": None,
    },
    "not_follow": {
        "system_file": "skill_not_follow.txt",
        "user_file": "skill_not_follow_user.txt",
        "output_prefix": "skill_not_follow",
        "single_trajectory": True,
        "raw_outcome": None,
        "skill_outcome": None,
    },
}


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


def load_prompt_files(prompt_type: str) -> tuple[str, str]:
    """Load system prompt and user prompt template for the given prompt_type."""
    cfg = PROMPT_CONFIGS[prompt_type]
    system_prompt = (SCRIPT_DIR / cfg["system_file"]).read_text()
    user_template = (SCRIPT_DIR / cfg["user_file"]).read_text()
    return system_prompt, user_template


def parse_log_filename(filename: str, prefix: str) -> tuple[str, str] | None:
    """Parse a log filename into (instance_id, outcome).

    Examples:
        "cli_only_agent_10452_SUCCEED.md"  -> ("10452", "SUCCEED")
        "cli_skill_agent_105-24_FAILED.md" -> ("105-24", "FAILED")
    """
    name = os.path.splitext(filename)[0]
    if not name.startswith(prefix):
        return None
    name = name[len(prefix):]
    match = re.match(r"^(.+)_(SUCCEED|FAILED)$", name)
    if not match:
        return None
    return match.group(1), match.group(2)


def strip_raw_log_metadata(text: str) -> str:
    """Remove header timestamp block and trailing result block from raw logs."""
    text = re.sub(
        r"\A# Chat History:.*?\n\n\*\*Timestamp\*\*:[^\n]*\n\n---\n\n",
        "",
        text,
        flags=re.DOTALL,
    )
    text = re.sub(r"\n---\n\n## RESULT\n.*\Z", "", text, flags=re.DOTALL)
    return text


def parse_total_turns(text: str) -> int | None:
    matches = re.findall(r"Total Turns:\s*(\d+)", text)
    if not matches:
        return None
    return int(matches[-1])


def extract_assistant_messages(text: str) -> list[str]:
    text = re.sub(r"\n---\n\n## RESULT\n.*\Z", "", text, flags=re.DOTALL)
    messages = []
    pattern = re.compile(r"^## \[\d+\] ASSISTANT\n\n(.*?)(?=^## \[|\Z)", re.DOTALL | re.MULTILINE)
    for match in pattern.finditer(text):
        msg = match.group(1).strip()
        if msg:
            messages.append(msg)
    return messages


def compute_log_stats(paths: list[str], tokenizer: AutoTokenizer) -> tuple[float | None, float | None, int, int, int]:
    total_turns = 0
    total_tokens = 0
    turns_count = 0
    tokens_count = 0
    missing_turns = 0

    for path in paths:
        text = Path(path).read_text()
        turns = parse_total_turns(text)
        if turns is None:
            missing_turns += 1
        else:
            total_turns += turns
            turns_count += 1

        messages = extract_assistant_messages(text)
        if messages:
            tokens = sum(len(tokenizer.encode(m, add_special_tokens=False)) for m in messages)
            total_tokens += tokens
            tokens_count += 1

    avg_turns = (total_turns / turns_count) if turns_count else None
    avg_tokens = (total_tokens / tokens_count) if tokens_count else None
    return avg_turns, avg_tokens, turns_count, tokens_count, missing_turns


def find_trajectory_pairs(
    raw_log_dir: str,
    skill_log_dir: str,
    raw_outcome_filter: str | None = None,
    skill_outcome_filter: str | None = None,
    raw_agent_prefix: str = "cli_only_agent_",
    skill_agent_prefix: str = "cli_skill_agent_",
) -> dict[str, tuple[str, str]]:
    """Find matching (raw, skill) trajectory file pairs by instance ID.

    Filters to only pairs where each side's outcome matches the required filter
    (if specified).

    Returns:
        Dict mapping instance_id -> (raw_log_path, skill_log_path),
        sorted by instance_id.
    """
    raw_files = {}
    for f in os.listdir(raw_log_dir):
        parsed = parse_log_filename(f, raw_agent_prefix)
        if not parsed:
            continue
        iid, outcome = parsed
        if raw_outcome_filter and outcome != raw_outcome_filter:
            continue
        raw_files[iid] = os.path.join(raw_log_dir, f)

    skill_files = {}
    for f in os.listdir(skill_log_dir):
        parsed = parse_log_filename(f, skill_agent_prefix)
        if not parsed:
            continue
        iid, outcome = parsed
        if skill_outcome_filter and outcome != skill_outcome_filter:
            continue
        skill_files[iid] = os.path.join(skill_log_dir, f)

    common_ids = set(raw_files.keys()) & set(skill_files.keys())
    return {iid: (raw_files[iid], skill_files[iid]) for iid in sorted(common_ids)}


def find_skill_trajectories(
    skill_log_dir: str,
    outcome_filter: str | None = None,
    skill_agent_prefix: str = "cli_skill_agent_",
) -> dict[str, str]:
    """Find skill agent trajectory files, optionally filtered by outcome.

    Returns:
        Dict mapping instance_id -> skill_log_path, sorted by instance_id.
    """
    skill_files = {}
    for f in os.listdir(skill_log_dir):
        parsed = parse_log_filename(f, skill_agent_prefix)
        if not parsed:
            continue
        iid, outcome = parsed
        if outcome_filter and outcome != outcome_filter:
            continue
        skill_files[iid] = os.path.join(skill_log_dir, f)
    return dict(sorted(skill_files.items()))


def analyze_instance(
    client: OpenAI,
    model: str,
    system_prompt: str,
    user_template: str,
    skill_path: str,
    raw_path: str | None = None,
    generation_config: dict | None = None,
) -> str:
    """Call the vLLM model to produce a behavior analysis.

    When raw_path is None (single-trajectory mode), only
    {agent_with_skill_trajectory} is formatted into the user template.
    """
    format_kwargs: dict[str, str] = {
        "agent_with_skill_trajectory": Path(skill_path).read_text(),
    }
    if raw_path is not None:
        raw_text = Path(raw_path).read_text()
        format_kwargs["raw_agent_trajectory"] = strip_raw_log_metadata(raw_text)

    user_message = user_template.format(**format_kwargs)

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
    return response.choices[0].message.content


def main():
    parser = argparse.ArgumentParser(
        description="Analyze behavior differences between raw and skill-augmented agents using vLLM"
    )
    parser.add_argument(
        "--raw_log_dir",
        type=str,
        default=None,
        help="Directory with raw agent trajectories (cli_only_logs). "
             "Required for all prompt types except 'utility'.",
    )
    parser.add_argument(
        "--skill_log_dir",
        type=str,
        required=True,
        help="Directory with skill-augmented agent trajectories (cli_skill_logs)",
    )
    parser.add_argument(
        "--output_dir",
        type=str,
        required=True,
        help="Directory to save per-instance analysis results",
    )
    parser.add_argument(
        "--prompt_type",
        type=str,
        choices=PROMPT_CONFIGS.keys(),
        default="reflection",
        help="Analysis type: reflection (all pairs), success (raw FAILED→skill SUCCEED), "
             "failure (raw SUCCEED→skill FAILED), utility (skill logs), not_follow (skill logs)",
    )
    parser.add_argument(
        "--instance_id",
        type=str,
        default=None,
        help="Analyze only this instance ID (default: all matched pairs)",
    )
    parser.add_argument(
        "--raw_agent_prefix",
        type=str,
        default="cli_only_agent_",
        help="Prefix for raw agent trajectory filenames (default: cli_only_agent_)",
    )
    parser.add_argument(
        "--skill_agent_prefix",
        type=str,
        default="cli_skill_agent_",
        help="Prefix for skill agent trajectory filenames (default: cli_skill_agent_)",
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
        "--tokenizer_model",
        type=str,
        default=None,
        help="Tokenizer model for token counting (default: TOKENIZER_MODEL env var or gpt2)",
    )
    parser.add_argument(
        "--max_workers",
        type=int,
        default=None,
        help="Max threads for parallel API calls (default: min(32, cpu*4))",
    )
    args = parser.parse_args()
    generation_config = build_generation_config(args)

    cfg = PROMPT_CONFIGS[args.prompt_type]
    cfg["raw_agent_prefix"] = args.raw_agent_prefix
    cfg["skill_agent_prefix"] = args.skill_agent_prefix

    # Resolve model
    model = args.model or os.getenv("OPENAI_MODEL")
    if not model:
        print("Error: model must be specified via --model or OPENAI_MODEL env var", file=sys.stderr)
        sys.exit(1)

    # Initialize OpenAI-compatible client pointing to vLLM
    base_url = os.getenv("OPENAI_BASE_URL", "http://localhost:8000/v1")
    api_key = os.getenv("OPENAI_API_KEY", "EMPTY")
    client = OpenAI(api_key=api_key, base_url=base_url)

    # Initialize tokenizer for token counting
    tokenizer_model = args.tokenizer_model or os.getenv("TOKENIZER_MODEL") or "gpt2"
    tokenizer = AutoTokenizer.from_pretrained(tokenizer_model)

    # Validate: --raw_log_dir is required for all pair-based modes
    if not cfg["single_trajectory"] and not args.raw_log_dir:
        print(f"Error: --raw_log_dir is required for prompt_type='{args.prompt_type}'.", file=sys.stderr)
        sys.exit(1)

    # Load prompt files for this analysis type
    system_prompt, user_template = load_prompt_files(args.prompt_type)

    # Build work items: dict of instance_id -> (raw_path | None, skill_path)
    if cfg["single_trajectory"]:
        skill_files = find_skill_trajectories(
            args.skill_log_dir,
            cfg["skill_outcome"],
            skill_agent_prefix=cfg["skill_agent_prefix"],
        )
        work_items = {iid: (None, path) for iid, path in skill_files.items()}
    else:
        work_items = find_trajectory_pairs(
            args.raw_log_dir,
            args.skill_log_dir,
            raw_outcome_filter=cfg["raw_outcome"],
            skill_outcome_filter=cfg["skill_outcome"],
            raw_agent_prefix=cfg["raw_agent_prefix"],
            skill_agent_prefix=cfg["skill_agent_prefix"],
        )

    if not work_items:
        print(f"No trajectories found for prompt_type='{args.prompt_type}'.", file=sys.stderr)
        sys.exit(1)

    # Filter to a single instance if requested
    if args.instance_id:
        if args.instance_id not in work_items:
            print(f"Instance ID '{args.instance_id}' not found.", file=sys.stderr)
            sys.exit(1)
        work_items = {args.instance_id: work_items[args.instance_id]}

    print(f"Prompt type:      {args.prompt_type}")
    print(f"Trajectories:     {len(work_items)}")
    print(f"Model:            {model}")
    print(f"Endpoint:         {base_url}")
    print(f"Output dir:       {args.output_dir}")
    print("-" * 60)

    os.makedirs(args.output_dir, exist_ok=True)

    output_prefix = cfg["output_prefix"]
    total = len(work_items)
    max_workers = args.max_workers or min(32, (os.cpu_count() or 1) * 4)

    raw_log_paths = [raw for raw, _ in work_items.values() if raw]
    skill_log_paths = [skill for _, skill in work_items.values()]

    if raw_log_paths:
        avg_turns, avg_tokens, turns_count, tokens_count, missing_turns = compute_log_stats(
            raw_log_paths, tokenizer
        )
        print(
            f"Raw avg turns:   {avg_turns:.2f}" if avg_turns is not None else "Raw avg turns:   N/A"
        )
        print(
            f"Raw avg tokens:  {avg_tokens:.2f}" if avg_tokens is not None else "Raw avg tokens:  N/A"
        )
        if missing_turns:
            print(f"Raw missing turns: {missing_turns}")

    avg_turns, avg_tokens, turns_count, tokens_count, missing_turns = compute_log_stats(
        skill_log_paths, tokenizer
    )
    print(
        f"Skill avg turns: {avg_turns:.2f}" if avg_turns is not None else "Skill avg turns: N/A"
    )
    print(
        f"Skill avg tokens:{avg_tokens:.2f}" if avg_tokens is not None else "Skill avg tokens: N/A"
    )
    if missing_turns:
        print(f"Skill missing turns: {missing_turns}")

    def run_analysis(iid: str, raw_path: str | None, skill_path: str, output_path: str) -> str:
        result = analyze_instance(
            client,
            model,
            system_prompt,
            user_template,
            skill_path,
            raw_path,
            generation_config,
        )
        Path(output_path).write_text(result)
        return output_path

    # Process each item with a ThreadPool and tqdm progress bar
    tasks = []
    skipped = 0
    for iid, (raw_path, skill_path) in work_items.items():
        output_path = os.path.join(args.output_dir, f"{output_prefix}_{iid}.md")
        if os.path.isfile(output_path):
            skipped += 1
            continue
        tasks.append((iid, raw_path, skill_path, output_path))

    if skipped:
        print(f"Skipped (already exists): {skipped}")

    if not tasks:
        print("No remaining trajectories to analyze.")
        return

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(run_analysis, iid, raw_path, skill_path, output_path): iid
            for iid, raw_path, skill_path, output_path in tasks
        }
        with tqdm(total=len(futures), unit="prompt", desc="Analyzing") as pbar:
            for future in as_completed(futures):
                iid = futures[future]
                try:
                    output_path = future.result()
                    tqdm.write(f"{iid}: done -> {output_path}")
                except Exception as e:
                    tqdm.write(f"{iid}: ERROR: {e}")
                finally:
                    pbar.update(1)


if __name__ == "__main__":
    main()
