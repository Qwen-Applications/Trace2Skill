from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

INDUCTION_TAG = "item_extraction"


class ArtifactValidationError(ValueError):
    """Raised when an LLM artifact payload is missing required structure."""


_ITEM_PATTERN = re.compile(
    r"^#\s+(Generalizable Script Item|Script Fix Item)\s+(\d+)\s*\n"
    r"(.*?)(?=^#\s+(?:Generalizable Script Item|Script Fix Item)\s+\d+\s*$|\Z)",
    re.MULTILINE | re.DOTALL,
)
_SECTION_RE = re.compile(r"^##\s+{name}\s*\n(.*?)(?=^##\s+|\Z)", re.MULTILINE | re.DOTALL)
def extract_tagged_json(text: str, tag: str) -> dict[str, Any]:
    """Extract a tagged JSON object from final agent text."""
    pattern = rf"<{re.escape(tag)}>\s*(.*?)\s*</{re.escape(tag)}>"
    match = re.search(pattern, text, re.DOTALL)
    payload = match.group(1).strip() if match else text.strip()
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise ArtifactValidationError(f"<{tag}> payload must be a JSON object")
    return data


def _strip_think(text: str) -> str:
    return text.rsplit("</think>", 1)[-1] if "</think>" in text else text


def _strip_outer_code_fence(text: str) -> str:
    """Strip one outer code fence if the whole payload is wrapped in one."""
    stripped = text.strip()
    if not stripped.startswith("```") or not stripped.endswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) < 2:
        return stripped
    if not lines[0].startswith("```"):
        return stripped
    if lines[-1].strip() != "```":
        return stripped
    return "\n".join(lines[1:-1]).strip()


def _extract_section(body: str, name: str) -> str:
    match = re.search(_SECTION_RE.pattern.format(name=re.escape(name)), body, re.MULTILINE | re.DOTALL)
    return match.group(1).strip() if match else ""


def _extract_metadata_text(body: str) -> str:
    section = _extract_section(body, "Metadata")
    if not section:
        raise ArtifactValidationError("induction item: missing 'Metadata' section")
    return section.strip()


def parse_induction_markdown(text: str) -> dict[str, Any]:
    """Parse the stage-1 markdown output into normalized items."""
    text = _strip_think(text)
    text = _strip_outer_code_fence(text)
    items: list[dict[str, Any]] = []
    titles_seen: set[str] = set()

    for idx, match in enumerate(_ITEM_PATTERN.finditer(text), start=1):
        raw_type = match.group(1)
        body = match.group(3).strip()
        title = _extract_section(body, "Title")
        description = _extract_section(body, "Description")
        generalizability = _extract_section(body, "Generalizability")
        metadata = _extract_metadata_text(body)
        if not title:
            raise ArtifactValidationError(f"induction item {idx}: missing 'Title'")
        if not description:
            raise ArtifactValidationError(f"induction item {idx}: missing 'Description'")
        if not generalizability:
            raise ArtifactValidationError(f"induction item {idx}: missing 'Generalizability'")
        if not metadata:
            raise ArtifactValidationError(f"induction item {idx}: missing 'Metadata'")
        if title in titles_seen:
            raise ArtifactValidationError(f"induction item {idx}: duplicate title {title!r}")
        titles_seen.add(title)

        item_type = "generalizable_script" if raw_type == "Generalizable Script Item" else "script_fix"
        item_id = f"item_{idx:04d}"

        items.append(
            {
                "item_id": item_id,
                "item_type": item_type,
                "title": title,
                "description": description,
                "generalizability": generalizability,
                "metadata": metadata,
            }
        )

    if len(items) > 3:
        raise ArtifactValidationError(f"induction items: expected at most 3 items, got {len(items)}")

    return validate_induction_items(items)


def validate_induction_items(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Validate and split normalized induction items."""
    if len(items) > 3:
        raise ArtifactValidationError(f"induction items: expected at most 3 items, got {len(items)}")
    generalizable_script_items = [item for item in items if item["item_type"] == "generalizable_script"]
    script_fix_items = [item for item in items if item["item_type"] == "script_fix"]
    return {
        "items": items,
        "generalizable_script_items": generalizable_script_items,
        "script_fix_items": script_fix_items,
    }


def _require_str(data: dict[str, Any], key: str, ctx: str) -> str:
    value = data.get(key, "")
    if not isinstance(value, str) or not value.strip():
        raise ArtifactValidationError(f"{ctx}: missing non-empty string field '{key}'")
    return value


def _require_list(data: dict[str, Any], key: str, ctx: str) -> list[Any]:
    value = data.get(key)
    if not isinstance(value, list):
        raise ArtifactValidationError(f"{ctx}: field '{key}' must be a list")
    return value


def _validate_runtime_test(item_id: str, item_type: str, item: dict[str, Any]) -> dict[str, Any]:
    ctx = f"{item_id}.reality_test"
    input_files = _require_list(item, "input_files", ctx)
    reference_files = _require_list(item, "reference_files", ctx)
    script_args = _require_list(item, "script_args", ctx)
    validator_config = item.get("validator_config", {})
    if not isinstance(validator_config, dict):
        raise ArtifactValidationError(f"{ctx}: validator_config must be an object")
    provenance = item.get("reference_provenance", {})
    if not isinstance(provenance, dict):
        raise ArtifactValidationError(f"{ctx}: reference_provenance must be an object")

    normalized: dict[str, Any] = {
        "test_id": _require_str(item, "test_id", ctx),
        "test_type": _require_str(item, "test_type", ctx),
        "purpose": _require_str(item, "purpose", ctx),
        "input_files": [str(x) for x in input_files],
        "reference_files": [str(x) for x in reference_files],
        "script_args": [str(x) for x in script_args],
        "hypothesis_output": _require_str(item, "hypothesis_output", ctx),
        "validator_type": _require_str(item, "validator_type", ctx),
        "validator_path": _require_str(item, "validator_path", ctx),
        "validator_config": {str(k): v for k, v in validator_config.items()},
        "expected_exit_code": int(item.get("expected_exit_code", 0)),
        "reality_basis": _require_str(item, "reality_basis", ctx),
        "reference_provenance": {str(k): v for k, v in provenance.items()},
    }
    if item_type == "script_fix":
        normalized["original_expected_to_fail"] = bool(item.get("original_expected_to_fail", True))
        normalized["fixed_expected_exit_code"] = int(item.get("fixed_expected_exit_code", 0))
    return normalized


def validate_item_extraction_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate the stage-2 extraction payload."""
    item_id = _require_str(payload, "item_id", "item_extraction")
    item_type = _require_str(payload, "item_type", "item_extraction")
    if item_type not in {"generalizable_script", "script_fix"}:
        raise ArtifactValidationError(f"item_extraction: invalid item_type {item_type!r}")
    title = _require_str(payload, "title", "item_extraction")

    raw_script = payload.get("script_artifact")
    raw_fix = payload.get("fix_artifact")
    if (raw_script is None) == (raw_fix is None):
        raise ArtifactValidationError("item_extraction: exactly one of script_artifact or fix_artifact is required")

    script_artifact = None
    fix_artifact = None
    if raw_script is not None:
        if not isinstance(raw_script, dict):
            raise ArtifactValidationError("item_extraction: script_artifact must be an object")
        script_artifact = {
            "name": _require_str(raw_script, "name", f"{item_id}.script_artifact"),
            "docstring": _require_str(raw_script, "docstring", f"{item_id}.script_artifact"),
            "code": _require_str(raw_script, "code", f"{item_id}.script_artifact"),
            "args_spec": [str(x) for x in _require_list(raw_script, "args_spec", f"{item_id}.script_artifact")],
        }
    if raw_fix is not None:
        if not isinstance(raw_fix, dict):
            raise ArtifactValidationError("item_extraction: fix_artifact must be an object")
        fix_artifact = {
            "target_script": _require_str(raw_fix, "target_script", f"{item_id}.fix_artifact"),
            "failure_mode": _require_str(raw_fix, "failure_mode", f"{item_id}.fix_artifact"),
            "proposed_fix_summary": _require_str(
                raw_fix,
                "proposed_fix_summary",
                f"{item_id}.fix_artifact",
            ),
            "fixed_code": _require_str(raw_fix, "fixed_code", f"{item_id}.fix_artifact"),
        }

    reality_suite = payload.get("reality_suite")
    if not isinstance(reality_suite, dict):
        raise ArtifactValidationError("item_extraction: reality_suite must be an object")
    tests = _require_list(reality_suite, "tests", f"{item_id}.reality_suite")
    if not tests:
        raise ArtifactValidationError(f"{item_id}.reality_suite: at least one test is required")
    scenario_verification = reality_suite.get("scenario_verification", {})
    if not isinstance(scenario_verification, dict):
        raise ArtifactValidationError(f"{item_id}.reality_suite: scenario_verification must be an object")
    if not str(scenario_verification.get("verification_notes", "")).strip():
        raise ArtifactValidationError(
            f"{item_id}.reality_suite: scenario_verification.verification_notes is required"
        )

    normalized_tests = [_validate_runtime_test(item_id, item_type, test) for test in tests]
    return {
        "item_id": item_id,
        "item_type": item_type,
        "title": title,
        "script_artifact": script_artifact,
        "fix_artifact": fix_artifact,
        "reality_suite": {
            "tests": normalized_tests,
            "scenario_verification": {
                "used_real_input": bool(scenario_verification.get("used_real_input", False)),
                "used_real_output": bool(scenario_verification.get("used_real_output", False)),
                "verification_notes": str(scenario_verification.get("verification_notes", "")).strip(),
            },
        },
    }


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def render_script_induction_report(
    *,
    instance_id: str,
    induction_markdown: str,
    items: list[dict[str, Any]],
    item_extractions: list[dict[str, Any]],
    verification_results: list[dict[str, Any]],
) -> str:
    lines: list[str] = [f"# Script Induction Report: {instance_id}", ""]
    lines.extend(["## Stage 1 Output", "", induction_markdown.strip() or "(none)", ""])
    latest_by_item = {entry["item_id"]: entry for entry in item_extractions}
    verification_by_item = {entry["item_id"]: entry for entry in verification_results}

    for item in items:
        lines.extend(
            [
                f"## {item['item_id']} - {item['title']}",
                f"type: {item['item_type']}",
                "",
                "### Description",
                item["description"],
                "",
                "### Generalizability",
                item["generalizability"],
                "",
                "### Metadata",
                item["metadata"],
            ]
        )
        extraction = latest_by_item.get(item["item_id"])
        if extraction:
            lines.extend(["", "### Extracted Artifact"])
            if extraction.get("script_artifact"):
                script = extraction["script_artifact"]
                lines.extend(
                    [
                        f"name: {script['name']}",
                        "```python",
                        script["code"].rstrip(),
                        "```",
                    ]
                )
            if extraction.get("fix_artifact"):
                fix = extraction["fix_artifact"]
                lines.extend(
                    [
                        f"target_script: {fix['target_script']}",
                        "```python",
                        fix["fixed_code"].rstrip(),
                        "```",
                    ]
                )
            lines.extend(["", "### Reality Tests"])
            for test in extraction["reality_suite"]["tests"]:
                lines.extend(
                    [
                        f"- {test['test_id']} ({test['test_type']}): {test['purpose']}",
                        f"  input: {', '.join(test['input_files'])}",
                        f"  reference: {', '.join(test['reference_files'])}",
                        f"  hypothesis: {test['hypothesis_output']}",
                    ]
                )
        verification = verification_by_item.get(item["item_id"])
        if verification:
            lines.extend(
                [
                    "",
                    "### Verification",
                    f"status: {verification.get('status', 'unknown')}",
                    f"attempts: {verification.get('attempts', 0)}",
                    f"tests_passed: {verification.get('tests_passed', 0)}",
                    f"tests_failed: {verification.get('tests_failed', 0)}",
                ]
            )
        lines.extend(["", "---", ""])

    return "\n".join(lines).rstrip() + "\n"
