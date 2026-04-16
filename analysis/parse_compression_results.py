#!/usr/bin/env python3
"""
Parse compression markdown outputs into structured JSON.

Each pattern is extracted into: index, title, description, frequency,
source_cases, covered_items. The batch ID is inferred from the filename.
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path


PATTERN_HEADER = r"Pattern"
ITEM_PATTERN = re.compile(
    r"^#\s+Pattern\s+(\d+)\s*\n"
    r"(.*?)(?=\n#\s+Pattern\s+\d+|\Z)",
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


def parse_patterns(text: str) -> list[dict]:
    text = strip_think_prefix(text)
    text = strip_code_fences(text)
    patterns = []

    for match in ITEM_PATTERN.finditer(text):
        index = int(match.group(1))
        body = match.group(2).strip()
        source_cases = extract_section(body, "Source Cases")
        case_ids = re.findall(r"\b\d+(?:-\d+)?\b", source_cases)
        case_indices = []
        for token in case_ids:
            try:
                case_indices.append(int(token))
            except ValueError:
                continue
        patterns.append({
            "index": index,
            "title": extract_section(body, "Title"),
            "description": extract_section(body, "Description"),
            "frequency": extract_section(body, "Frequency"),
            "source_cases": source_cases,
            "source_case_ids": case_ids,
            "source_case_indices": case_indices,
            "covered_items": extract_section(body, "Covered Items"),
        })

    return patterns


def infer_batch_id(filename: str) -> str:
    name = os.path.splitext(os.path.basename(filename))[0]
    if name.startswith("compression_"):
        return name[len("compression_"):]
    return name


def parse_file(filepath: str) -> dict:
    text = Path(filepath).read_text()
    patterns = parse_patterns(text)
    map_path = Path(filepath).with_suffix(".map.json")
    id_map = None
    if map_path.is_file():
        try:
            id_map = json.loads(map_path.read_text())
        except json.JSONDecodeError:
            id_map = None
    if id_map:
        id_map_dict = {str(i): iid for i, iid in enumerate(id_map)}
        for pat in patterns:
            indices = pat.get("source_case_indices", [])
            mapped = [id_map[i] for i in indices if 0 <= i < len(id_map)]
            pat["source_case_task_ids"] = mapped
    return {
        "batch_id": infer_batch_id(filepath),
        "source_file": os.path.basename(filepath),
        "id_map": id_map,
        "id_map_dict": id_map_dict if id_map else None,
        "patterns": patterns,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Parse compression markdown outputs into structured JSON"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--input",
        type=str,
        help="Single compression markdown file to parse",
    )
    group.add_argument(
        "--input_dir",
        type=str,
        help="Directory of compression markdown files to parse",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output JSON file path (default: print to stdout)",
    )
    args = parser.parse_args()

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

    results = []
    for filepath in files:
        record = parse_file(filepath)
        if record["patterns"]:
            results.append(record)
        else:
            print(f"Warning: no patterns parsed from {filepath}", file=sys.stderr)

    output_json = json.dumps(results, indent=2)
    if args.output:
        Path(args.output).write_text(output_json)
        total_patterns = sum(len(r["patterns"]) for r in results)
        print(f"Wrote {len(results)} records ({total_patterns} patterns) to {args.output}")
    else:
        print(output_json)


if __name__ == "__main__":
    main()
