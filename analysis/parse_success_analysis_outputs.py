#!/usr/bin/env python3
"""
Parse success analysis outputs into structured JSON.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path


REPORT_BASENAME = "success_analysis.md"
REPORT_FILE_PATTERN = re.compile(r"^success_analysis_(.+)\.md$")
ITEM_HEADING_PATTERN = re.compile(r"^(#+)\s+Success Memory Item\s+(\d+)\s*$", re.MULTILINE)
SECTION_HEADING_PATTERN = re.compile(r"^(#+)\s+(Title|Description|Content)\s*$", re.MULTILINE)
FAILED_PARSE_DIRNAME = "failed_to_parse"


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


def clean_section_content(text: str) -> str:
    cleaned = re.sub(r"\n(?:---+|\*\*\*+|___+)\s*\Z", "", text.strip())
    return cleaned.strip()


def parse_items(text: str) -> list[dict]:
    text = strip_code_fences(strip_think_prefix(text))
    items = []
    item_matches = list(ITEM_HEADING_PATTERN.finditer(text))

    for idx, match in enumerate(item_matches):
        body_start = match.end()
        body_end = item_matches[idx + 1].start() if idx + 1 < len(item_matches) else len(text)
        body = text[body_start:body_end]
        sections: dict[str, str] = {}
        section_matches = list(SECTION_HEADING_PATTERN.finditer(body))

        for section_idx, section_match in enumerate(section_matches):
            name = section_match.group(2)
            content_start = section_match.end()
            content_end = (
                section_matches[section_idx + 1].start()
                if section_idx + 1 < len(section_matches)
                else len(body)
            )
            sections[name] = clean_section_content(body[content_start:content_end])

        items.append(
            {
                "type": "success_memory",
                "number": int(match.group(2)),
                "title": sections.get("Title", ""),
                "description": sections.get("Description", ""),
                "content": sections.get("Content", ""),
            }
        )

    return items


def parse_report(report_path: Path, instance_id: str) -> dict:
    text = report_path.read_text(encoding="utf-8", errors="replace")
    return {
        "instance_id": instance_id,
        "source_file": report_path.name,
        "items": parse_items(text),
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
        raise ValueError(f"Unrecognized success analysis filename: {instance_path.name}")
    return match.group(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse success analysis outputs")
    parser.add_argument(
        "--input_dir",
        required=True,
        help="run_success_analysis_llm.py output directory",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output JSON file path (default: print to stdout)",
    )
    args = parser.parse_args()

    instance_paths = collect_instances(args.input_dir)
    if not instance_paths:
        print("No success analysis reports found.", file=sys.stderr)
        sys.exit(1)

    results = []
    for instance_path in instance_paths:
        report_path = resolve_report_path(instance_path)
        record = parse_report(report_path, extract_instance_id(instance_path))
        if record["items"]:
            results.append(record)
        else:
            save_failed_parse_debug_artifacts(report_path, record["instance_id"])
            print(f"Warning: no items parsed from {report_path}", file=sys.stderr)

    output_json = json.dumps(results, indent=2)
    if args.output:
        Path(args.output).write_text(output_json, encoding="utf-8")
        total_items = sum(len(record["items"]) for record in results)
        print(f"Wrote {len(results)} records ({total_items} items) to {args.output}")
    else:
        print(output_json)


if __name__ == "__main__":
    main()
