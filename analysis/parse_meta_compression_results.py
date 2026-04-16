#!/usr/bin/env python3
"""
Parse meta-compression markdown outputs into structured JSON.
"""

import argparse
import json
import re
import sys
from pathlib import Path


ITEM_PATTERN = re.compile(
    r"^#\s+Meta-Pattern\s+(\d+)\s*\n"
    r"(.*?)(?=\n#\s+Meta-Pattern\s+\d+|\Z)",
    re.MULTILINE | re.DOTALL,
)

SECTION_PATTERN = re.compile(
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
    pattern = SECTION_PATTERN.pattern.format(name=re.escape(section_name))
    match = re.search(pattern, body, re.MULTILINE | re.DOTALL)
    return match.group(1).strip() if match else ""


def parse_meta_patterns(text: str) -> list[dict]:
    text = strip_think_prefix(text)
    text = strip_code_fences(text)
    patterns = []

    for match in ITEM_PATTERN.finditer(text):
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


def main():
    parser = argparse.ArgumentParser(
        description="Parse meta-compression markdown outputs into structured JSON"
    )
    parser.add_argument(
        "--input",
        type=str,
        required=True,
        help="Meta-compression markdown file to parse",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output JSON file path (default: print to stdout)",
    )
    args = parser.parse_args()

    text = Path(args.input).read_text()
    patterns = parse_meta_patterns(text)
    if not patterns:
        print("Warning: no meta-patterns parsed from input.", file=sys.stderr)

    output_json = json.dumps(patterns, indent=2)
    if args.output:
        Path(args.output).write_text(output_json)
        print(f"Wrote {len(patterns)} meta-patterns to {args.output}")
    else:
        print(output_json)


if __name__ == "__main__":
    main()
