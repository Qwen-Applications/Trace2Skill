#!/usr/bin/env python3
"""
Parse behavior analysis markdown outputs into structured JSON.

Handles all four analysis output types:
    - Behavior Change Items  (from prompt_type=reflection)
    - Success Cause Items    (from prompt_type=success)
    - Failure Cause Items    (from prompt_type=failure)
    - Skill Usage Items      (from prompt_type=utility)

Each item is extracted into:  type, number, title, description, content.
The instance ID is inferred from the output filename.

Usage:
    # Parse a single file to stdout
    python analysis/parse_analysis_results.py --input analysis/results/behavior_analysis_10452.md

    # Parse an entire directory and write JSON
    python analysis/parse_analysis_results.py \
        --input_dir analysis/results \
        --output analysis/results.json
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path


# Maps the markdown header text to the normalized type key used in JSON output.
ITEM_TYPE_MAP = {
    "Behavior Change Item": "behavior_change",
    "Success Cause Item": "success_cause",
    "Failure Cause Item": "failure_cause",
    "Skill Usage Item": "skill_usage",
    "Skill Omission Item": "skill_omission",
}

# Regex that matches a top-level item header and captures everything until
# the next item header or end of string.
ITEM_PATTERN = re.compile(
    r"^#\s+(Behavior Change Item|Success Cause Item|Failure Cause Item|Skill Usage Item|Skill Omission Item)\s+(\d+)\s*\n"
    r"(.*?)(?=\n#\s+(?:Behavior Change Item|Success Cause Item|Failure Cause Item|Skill Usage Item|Skill Omission Item)\s+\d+|\Z)",
    re.MULTILINE | re.DOTALL,
)

# Regex that captures text under a ## Section header until the next ## or end.
SECTION_PATTERN = re.compile(
    r"^##\s+{name}\s*\n(.*?)(?=\n##\s+|\Z)",
    re.MULTILINE | re.DOTALL,
)


def extract_section(body: str, section_name: str) -> str:
    """Extract and strip the text block under a given ## header."""
    pattern = SECTION_PATTERN.pattern.format(name=re.escape(section_name))
    match = re.search(pattern, body, re.MULTILINE | re.DOTALL)
    return match.group(1).strip() if match else ""


def strip_code_fences(text: str) -> str:
    """Remove wrapping triple-backtick code fences if the LLM wrapped its output."""
    stripped = text.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        # Remove opening fence (and optional language tag) and closing fence
        stripped = re.sub(r"^```\w*\n?", "", stripped)
        stripped = re.sub(r"\n?```$", "", stripped)
    return stripped


def strip_think_prefix(text: str) -> str:
    """If </think> appears, keep only content after the final tag."""
    if "</think>" not in text:
        return text
    return text.rsplit("</think>", 1)[-1]


def parse_items(text: str) -> list[dict]:
    """Parse all structured items from a single analysis output.

    Returns:
        List of dicts, each with keys: type, number, title, description, content.
    """
    text = strip_think_prefix(text)
    text = strip_code_fences(text)
    items = []

    for match in ITEM_PATTERN.finditer(text):
        item_type_raw = match.group(1)
        item_number = int(match.group(2))
        body = match.group(3).strip()

        items.append({
            "type": ITEM_TYPE_MAP[item_type_raw],
            "number": item_number,
            "title": extract_section(body, "Title"),
            "description": extract_section(body, "Description"),
            "content": extract_section(body, "Content"),
        })

    return items


def infer_instance_id(filename: str) -> str:
    """Extract instance ID from an output filename.

    Examples:
        "behavior_analysis_10452.md"  -> "10452"
        "success_cause_105-24.md"     -> "105-24"
        "failure_cause_11276.md"      -> "11276"
        "skill_utility_10452.md"      -> "10452"
    """
    name = os.path.splitext(os.path.basename(filename))[0]
    for prefix in ("behavior_analysis_", "success_cause_", "failure_cause_", "skill_utility_"):
        if name.startswith(prefix):
            return name[len(prefix):]
    for prefix in ("skill_not_follow_", "skill_omission_"):
        if name.startswith(prefix):
            return name[len(prefix):]
    return name


def parse_file(filepath: str) -> dict:
    """Parse a single analysis output file into a structured record."""
    text = Path(filepath).read_text()
    items = parse_items(text)
    return {
        "instance_id": infer_instance_id(filepath),
        "source_file": os.path.basename(filepath),
        "items": items,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Parse analysis markdown outputs into structured JSON"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--input",
        type=str,
        help="Single analysis markdown file to parse",
    )
    group.add_argument(
        "--input_dir",
        type=str,
        help="Directory of analysis markdown files to parse",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output JSON file path (default: print to stdout)",
    )
    args = parser.parse_args()

    # Collect input files
    if args.input:
        files = [args.input]
    else:
        files = sorted(
            os.path.join(args.input_dir, f)
            for f in os.listdir(args.input_dir)
            if f.endswith(".md")
        )

    if not files:
        print("No .md files found.", file=sys.stderr)
        sys.exit(1)

    # Parse all files
    results = []
    for filepath in files:
        record = parse_file(filepath)
        if record["items"]:
            results.append(record)
        else:
            print(f"Warning: no items parsed from {filepath}", file=sys.stderr)

    # Output
    output_json = json.dumps(results, indent=2)
    if args.output:
        Path(args.output).write_text(output_json)
        total_items = sum(len(r["items"]) for r in results)
        print(f"Wrote {len(results)} records ({total_items} items) to {args.output}")
    else:
        print(output_json)


if __name__ == "__main__":
    main()
