#!/usr/bin/env python3
"""
Map meta-patterns to raw task IDs using parsed compression results.

Inputs:
  - meta patterns JSON from parse_meta_compression_results.py
  - patterns JSON from parse_compression_results.py

Output:
  - JSON with meta-patterns and resolved raw task IDs.
"""

import argparse
import json
import re
import sys
from pathlib import Path


def load_json(path: str):
    return json.loads(Path(path).read_text())


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def build_pattern_index(records: list[dict]) -> list[dict]:
    patterns = []
    for record in records:
        batch_id = record.get("batch_id", "unknown")
        for pat in record.get("patterns", []):
            patterns.append({
                "batch_id": batch_id,
                "index": pat.get("index"),
                "title": pat.get("title", ""),
                "description": pat.get("description", ""),
                "covered_items": pat.get("covered_items", ""),
                "frequency": pat.get("frequency", ""),
                "title_norm": normalize(pat.get("title", "")),
                "source_case_task_ids": pat.get("source_case_task_ids", []),
            })
    return patterns


def extract_pattern_indices(text: str) -> list[int]:
    """Extract pattern indices from text, only matching 'Pattern X:' format."""
    indices = []
    # Match "Pattern X:" where X is the pattern index
    for match in re.finditer(r"Pattern\s+0*(\d+)\s*:", text, re.IGNORECASE):
        try:
            indices.append(int(match.group(1)))
        except ValueError:
            continue
    return indices


def extract_batch_pattern_pairs(text: str) -> list[tuple[str, int]]:
    """Extract (batch_id, pattern_index) pairs from text.
    
    Handles formats:
    - "Pattern X: ... (Batch Y)"
    - "Batch Y: Pattern X"
    """
    pairs = []
    # Format: "Pattern X: ... (Batch Y)"
    for match in re.finditer(r"Pattern\s+0*(\d+)\s*:.*?\(Batch\s+0*(\d+)\)", text, re.IGNORECASE):
        idx = int(match.group(1))
        batch = match.group(2).zfill(4)
        pairs.append((batch, idx))
    # Format: "Batch Y: Pattern X" or "Batch Y - Pattern X"
    for match in re.finditer(r"Batch\s+0*(\d+)\s*[:\-]?\s*Pattern\s+0*(\d+)", text, re.IGNORECASE):
        batch = match.group(1).zfill(4)
        idx = int(match.group(2))
        pairs.append((batch, idx))
    return pairs


def match_patterns(covered_text: str, patterns: list[dict]) -> list[dict]:
    covered_norm = normalize(covered_text)
    matched = []
    pairs = extract_batch_pattern_pairs(covered_text)
    if pairs:
        # Primary matching: use explicit batch-pattern pairs
        for pat in patterns:
            for batch_id, idx in pairs:
                if pat["batch_id"] == batch_id and pat["index"] == idx:
                    matched.append(pat)
                    break
    else:
        # Fallback: extract pattern indices only if no batch-pattern pairs found
        indices = extract_pattern_indices(covered_text)
        if indices:
            for pat in patterns:
                if pat["index"] in indices:
                    matched.append(pat)
    # Also match by title substring (but only if not already matched)
    matched_keys = {(pat["batch_id"], pat["index"]) for pat in matched}
    for pat in patterns:
        key = (pat["batch_id"], pat["index"])
        if key not in matched_keys and pat["title_norm"] and pat["title_norm"] in covered_norm:
            matched.append(pat)
    # De-dup by (batch_id, index)
    seen = set()
    unique = []
    for pat in matched:
        key = (pat["batch_id"], pat["index"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(pat)
    return unique


def build_task_index(analysis_records: list[dict]) -> dict[str, list[dict]]:
    task_index = {}
    for record in analysis_records:
        iid = record.get("instance_id")
        if iid is None:
            continue
        task_index[iid] = record.get("items", [])
    return task_index


def main():
    parser = argparse.ArgumentParser(
        description="Map meta-patterns to raw task IDs using parsed patterns"
    )
    parser.add_argument(
        "--meta_json",
        type=str,
        required=True,
        help="JSON output from parse_meta_compression_results.py",
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
        help="Optional JSON output from parse_analysis_results.py for task items",
    )
    parser.add_argument(
        "--output",
        type=str,
        required=True,
        help="Output JSON path",
    )
    args = parser.parse_args()

    meta = load_json(args.meta_json)
    patterns_records = load_json(args.patterns_json)
    analysis_records = load_json(args.analysis_json) if args.analysis_json else []
    if not isinstance(meta, list) or not isinstance(patterns_records, list):
        print("Error: inputs must be JSON lists.", file=sys.stderr)
        sys.exit(1)

    all_patterns = build_pattern_index(patterns_records)
    if not all_patterns:
        print("Error: no patterns found in patterns_json.", file=sys.stderr)
        sys.exit(1)

    task_index = build_task_index(analysis_records) if analysis_records else {}

    results = []
    for mp in meta:
        covered = mp.get("covered_patterns", "")
        matched = match_patterns(covered, all_patterns)
        task_ids = sorted({tid for pat in matched for tid in pat["source_case_task_ids"]})
        patterns_tree = []
        for pat in matched:
            tasks = []
            for tid in pat["source_case_task_ids"]:
                task_entry = {"task_id": tid}
                if task_index:
                    task_entry["items"] = task_index.get(tid, [])
                tasks.append(task_entry)
            patterns_tree.append({
                "batch_id": pat["batch_id"],
                "index": pat["index"],
                "title": pat["title"],
                "description": pat["description"],
                "frequency": pat["frequency"],
                "covered_items": pat["covered_items"],
                "tasks": tasks,
            })
        results.append({
            "meta_index": mp.get("index"),
            "meta_title": mp.get("title"),
            "meta_description": mp.get("description"),
            "covered_patterns": covered,
            "matched_patterns": patterns_tree,
            "raw_task_ids": task_ids,
        })

    Path(args.output).write_text(json.dumps(results, indent=2))
    print(f"Wrote {len(results)} meta-pattern mappings to {args.output}")


if __name__ == "__main__":
    main()
