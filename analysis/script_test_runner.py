from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any

from evaluation_official import compare_workbooks


class TestExecutionError(ValueError):
    """Raised for invalid test definitions before or during running."""


SUPPORTED_CANDIDATE_VALIDATORS = {"spreadsheet_compare_workbooks"}


def validate_item_reality_suite_runtime(
    reality_suite: dict[str, Any],
    induction_dir: Path,
) -> None:
    """Runtime validation for generated reality suites."""
    tests = reality_suite.get("tests", [])
    if not tests:
        raise TestExecutionError("Need at least one reality test")

    for test in tests:
        test_id = str(test.get("test_id", "unknown"))
        validator_type = str(test.get("validator_type", "")).strip()
        if validator_type not in SUPPORTED_CANDIDATE_VALIDATORS:
            raise TestExecutionError(f"{test_id}: unsupported validator type {validator_type!r}")
        if not str(test.get("reality_basis", "")).strip():
            raise TestExecutionError(f"{test_id}: reality test missing reality_basis")

        for rel_path in test.get("input_files", []):
            full = (induction_dir / rel_path).resolve()
            root = (induction_dir / "test_inputs").resolve()
            if root not in full.parents:
                raise TestExecutionError(f"{test_id}: input file must live under test_inputs/: {rel_path}")
            if not full.exists():
                raise TestExecutionError(f"{test_id}: missing input file {rel_path}")

        reference_files = test.get("reference_files", [])
        if not isinstance(reference_files, list) or not reference_files:
            raise TestExecutionError(f"{test_id}: missing reference_files")
        for rel_path in reference_files:
            full = (induction_dir / rel_path).resolve()
            root = (induction_dir / "test_references").resolve()
            if root not in full.parents:
                raise TestExecutionError(
                    f"{test_id}: reference file must live under test_references/: {rel_path}"
                )
            if not full.exists():
                raise TestExecutionError(f"{test_id}: missing reference file {rel_path}")

        hypothesis_output = (induction_dir / str(test.get("hypothesis_output", ""))).resolve()
        outputs_root = (induction_dir / "test_outputs").resolve()
        if outputs_root not in hypothesis_output.parents:
            raise TestExecutionError(
                f"{test_id}: hypothesis_output must live under test_outputs/: {test.get('hypothesis_output', '')}"
            )

        validator_path = (induction_dir / str(test.get("validator_path", ""))).resolve()
        validators_root = (induction_dir / "validators").resolve()
        if validators_root not in validator_path.parents:
            raise TestExecutionError(
                f"{test_id}: validator_path must live under validators/: {test.get('validator_path', '')}"
            )

        validator_config = test.get("validator_config", {})
        if not isinstance(validator_config, dict):
            raise TestExecutionError(f"{test_id}: validator_config must be an object")
        if not str(validator_config.get("instruction_type", "")).strip():
            raise TestExecutionError(f"{test_id}: validator_config missing instruction_type")
        if not str(validator_config.get("answer_position", "")).strip():
            raise TestExecutionError(f"{test_id}: validator_config missing answer_position")

        provenance = test.get("reference_provenance", {})
        if not isinstance(provenance, dict):
            raise TestExecutionError(f"{test_id}: reference_provenance must be an object")
        mode = str(provenance.get("mode", "")).strip()
        if mode not in {"copied_from_agent_work", "manually_constructed"}:
            raise TestExecutionError(f"{test_id}: invalid reference_provenance.mode {mode!r}")


def run_item_verification(
    reality_suite: dict[str, Any],
    induction_dir: Path,
    script_path: Path,
    output_root: Path,
    timeout_sec: int,
) -> dict[str, Any]:
    """Run the proposed script against the generated reality suite."""
    output_root.mkdir(parents=True, exist_ok=True)
    per_test_results: list[dict[str, Any]] = []
    tests_passed = 0
    tests_failed = 0

    for test in reality_suite["tests"]:
        test_id = test["test_id"]
        cmd = ["python", str(script_path)] + [str(x) for x in test.get("script_args", [])]
        started = time.time()
        completed = subprocess.run(
            cmd,
            cwd=induction_dir,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
        duration = time.time() - started
        stdout_path = output_root / f"{test_id}.stdout.txt"
        stderr_path = output_root / f"{test_id}.stderr.txt"
        stdout_path.write_text(completed.stdout, encoding="utf-8")
        stderr_path.write_text(completed.stderr, encoding="utf-8")

        failures: list[str] = []
        expected_exit_code = int(test.get("fixed_expected_exit_code", test.get("expected_exit_code", 0)))
        if completed.returncode != expected_exit_code:
            failures.append(f"expected exit code {expected_exit_code}, got {completed.returncode}")

        hypothesis_path = (induction_dir / str(test["hypothesis_output"])).resolve()
        reference_path = (induction_dir / str(test["reference_files"][0])).resolve()
        compare_message = ""
        if not hypothesis_path.exists():
            failures.append(f"expected hypothesis output to exist: {hypothesis_path}")
            compare_passed = False
        else:
            compare_passed, compare_message = compare_workbooks(
                str(reference_path),
                str(hypothesis_path),
                str(test["validator_config"]["instruction_type"]),
                str(test["validator_config"]["answer_position"]),
            )
            if not compare_passed:
                failures.append(compare_message or "Workbook comparison failed")

        passed = not failures
        if passed:
            tests_passed += 1
        else:
            tests_failed += 1
        per_test_results.append(
            {
                "test_id": test_id,
                "cmd": cmd,
                "passed": passed,
                "exit_code": completed.returncode,
                "duration_sec": round(duration, 4),
                "stdout_path": str(stdout_path.relative_to(induction_dir)),
                "stderr_path": str(stderr_path.relative_to(induction_dir)),
                "validator_type": test["validator_type"],
                "validator_path": str(test["validator_path"]),
                "comparison_passed": compare_passed,
                "comparison_message": compare_message,
                "reference_file": str(test["reference_files"][0]),
                "hypothesis_file": str(test["hypothesis_output"]),
                "failure_summary": failures,
            }
        )

    failure_feedback = []
    for result in per_test_results:
        if result["passed"]:
            continue
        failure_feedback.append(f"{result['test_id']}: cmd={json.dumps(result['cmd'])}")
        for failure in result["failure_summary"]:
            failure_feedback.append(f"{result['test_id']}: {failure}")

    return {
        "tests_run": len(per_test_results),
        "tests_passed": tests_passed,
        "tests_failed": tests_failed,
        "overall_passed": tests_failed == 0,
        "per_test_results": per_test_results,
        "failure_feedback_for_agent": "\n".join(failure_feedback),
    }
