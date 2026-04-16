#!/usr/bin/env python3
"""
Map compression patterns to tasks, producing a tasks -> patterns tree.

Inputs:
  - patterns JSON from parse_compression_results.py
  - analysis JSON from parse_analysis_results.py (optional for item details)
"""

import argparse
import json
import sys
from pathlib import Path


def load_json(path: str):
    return json.loads(Path(path).read_text())


def build_task_index(analysis_records: list[dict]) -> dict[str, dict]:
    task_index = {}
    for record in analysis_records:
        iid = record.get("instance_id")
        if iid is None:
            continue
        task_index[iid] = {
            "task_id": iid,
            "items": record.get("items", []),
        }
    return task_index


def main():
    parser = argparse.ArgumentParser(
        description="Map compression patterns to tasks"
    )
    parser.add_argument(
        "--patterns_json",
        type=str,
        required=True,
        help="JSON output from parse_compression_results.py",
    )
    parser.add_argument(
        "--analysis_json",
        type=str,
        default=None,
        help="Optional JSON output from parse_analysis_results.py",
    )
    parser.add_argument(
        "--output",
        type=str,
        required=True,
        help="Output JSON path",
    )
    args = parser.parse_args()

    pattern_records = load_json(args.patterns_json)
    analysis_records = load_json(args.analysis_json) if args.analysis_json else []

    if not isinstance(pattern_records, list):
        print("Error: patterns_json must be a JSON list.", file=sys.stderr)
        sys.exit(1)

    task_index = build_task_index(analysis_records) if analysis_records else {}

    patterns = []
    for record in pattern_records:
        batch_id = record.get("batch_id", "unknown")
        for pat in record.get("patterns", []):
            tasks = []
            for tid in pat.get("source_case_task_ids", []):
                task = task_index.get(tid, {"task_id": tid, "items": []})
                tasks.append(task)
            patterns.append({
                "batch_id": batch_id,
                "index": pat.get("index"),
                "title": pat.get("title"),
                "description": pat.get("description"),
                "frequency": pat.get("frequency"),
                "covered_items": pat.get("covered_items"),
                "source_case_indices": pat.get("source_case_indices", []),
                "tasks": tasks,
            })

    output = {
        "patterns": patterns,
    }

    Path(args.output).write_text(json.dumps(output, indent=2))
    print(f"Wrote {len(patterns)} patterns to {args.output}")


if __name__ == "__main__":
    main()
