#!/usr/bin/env python3
"""
Parse error analysis outputs into structured JSON.

Supports both:
- run_error_analysis.py output directories that contain analysis_report.md
- run_error_analysis_llm.py flat files named error_analysis_<id>.md
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path


ITEM_TYPE_MAP = {
    "Failure Cause Item": "failure_cause",
    "Failure Memory Item": "failure_memory",
}
REPORT_BASENAME = "analysis_report.md"
REPORT_FILE_PATTERN = re.compile(r"^error_analysis_(.+)\.md$")
FAILED_PARSE_DIRNAME = "failed_to_parse"

ITEM_PATTERN = re.compile(
    r"^#\s+(Failure Cause Item|Failure Memory Item)\s+(\d+)\s*\n"
    r"(.*?)(?=\n#\s+(?:Failure Cause Item|Failure Memory Item)\s+\d+|\Z)",
    re.MULTILINE | re.DOTALL,
)

SECTION_PATTERN = re.compile(
    r"^##\s+{name}\s*\n(.*?)(?=\n##\s+|\Z)",
    re.MULTILINE | re.DOTALL,
)


def extract_section(body: str, section_name: str) -> str:
    pattern = SECTION_PATTERN.pattern.format(name=re.escape(section_name))
    match = re.search(pattern, body, re.MULTILINE | re.DOTALL)
    return match.group(1).strip() if match else ""


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


def parse_items(text: str) -> list[dict]:
    text = strip_think_prefix(text)
    text = strip_code_fences(text)
    items = []

    for match in ITEM_PATTERN.finditer(text):
        item_type_raw = match.group(1)
        item_number = int(match.group(2))
        body = match.group(3).strip()

        record = {
            "type": ITEM_TYPE_MAP[item_type_raw],
            "number": item_number,
            "title": extract_section(body, "Title"),
            "description": extract_section(body, "Description"),
            "content": extract_section(body, "Content"),
        }

        if record["type"] == "failure_cause":
            record["relation_to_skill"] = extract_section(body, "Relation to Skill")
        elif record["type"] == "failure_memory":
            record["skill_reflection"] = extract_section(body, "Skill Reflection")

        items.append(record)

    return items




def parse_report(report_path: Path, instance_id: str) -> dict:
    text = report_path.read_text(encoding="utf-8", errors="replace")
    items = parse_items(text)
    return {
        "instance_id": instance_id,
        "source_file": str(report_path.name),
        "items": items,
    }


def save_failed_parse_debug_artifacts(report_path: Path, instance_id: str) -> Path:
    """Copy reports with zero parsed items into a debug directory."""
    debug_dir = report_path.parent / FAILED_PARSE_DIRNAME / instance_id
    if report_path.parent.name == instance_id:
        debug_dir = report_path.parent.parent / FAILED_PARSE_DIRNAME / instance_id

    debug_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(report_path, debug_dir / report_path.name)
    return debug_dir


def collect_instances(output_dir: str) -> list[Path]:
    base = Path(output_dir)
    if not base.exists():
        return []
    collected = []
    for path in sorted(base.iterdir()):
        if path.is_dir() and (path / REPORT_BASENAME).exists():
            collected.append(path)
            continue
        if path.is_file() and REPORT_FILE_PATTERN.match(path.name):
            collected.append(path)
    return collected


def resolve_report_path(instance_path: Path) -> Path:
    return instance_path / REPORT_BASENAME if instance_path.is_dir() else instance_path


def extract_instance_id(instance_path: Path) -> str:
    if instance_path.is_dir():
        return instance_path.name
    match = REPORT_FILE_PATTERN.match(instance_path.name)
    if not match:
        raise ValueError(f"Unrecognized error analysis filename: {instance_path.name}")
    return match.group(1)


def main():
    parser = argparse.ArgumentParser(
        description="Parse error analysis outputs"
    )
    parser.add_argument(
        "--input_dir",
        required=True,
        help="run_error_analysis.py or run_error_analysis_llm.py output directory",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output JSON file path (default: print to stdout)",
    )
    args = parser.parse_args()

    instance_paths = collect_instances(args.input_dir)
    if not instance_paths:
        print("No error analysis reports found.", file=sys.stderr)
        sys.exit(1)

    total = 0
    passed = 0
    results = []

    for instance_path in instance_paths:
        report_path = resolve_report_path(instance_path)
        total += 1
        if instance_path.is_dir():
            pass_flag = instance_path / "evaluate_passed.flag"
            if not pass_flag.exists():
                continue
            passed += 1
        else:
            passed += 1
        record = parse_report(report_path, extract_instance_id(instance_path))
        if record["items"]:
            results.append(record)
        else:
            save_failed_parse_debug_artifacts(report_path, record["instance_id"])
            print(f"Warning: no items parsed from {report_path}", file=sys.stderr)

    pct = (passed / total * 100.0) if total else 0.0
    print(f"Passed evaluations: {passed}/{total} ({pct:.2f}%)")

    output_json = json.dumps(results, indent=2)
    if args.output:
        Path(args.output).write_text(output_json, encoding="utf-8")
        total_items = sum(len(r["items"]) for r in results)
        print(f"Wrote {len(results)} records ({total_items} items) to {args.output}")
    else:
        print(output_json)


if __name__ == "__main__":
    main()
