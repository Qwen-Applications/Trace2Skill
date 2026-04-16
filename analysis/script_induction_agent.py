#!/usr/bin/env python3
"""Simplified script induction pipeline with extraction, verification, and repair."""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from react_agent import AgentConfig, ReActAgent
from react_agent.models import Message, OpenAIClient
from spreadsheet_agent.agents.base import ChatHistoryLogger
from spreadsheet_agent.tools.bash import create_bash_tool

from analysis.script_induction_artifacts import (
    INDUCTION_TAG,
    ArtifactValidationError,
    extract_tagged_json,
    parse_induction_markdown,
    render_script_induction_report,
    save_json,
    validate_item_extraction_payload,
)
from analysis.script_test_runner import (
    TestExecutionError,
    run_item_verification,
    validate_item_reality_suite_runtime,
)

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
ANALYSIS_SYSTEM_PROMPT_PATH = SCRIPT_DIR / "script_induction_analysis_system_llm.txt"
ANALYSIS_USER_PROMPT_PATH = SCRIPT_DIR / "script_induction_analysis_user_llm.txt"
ANALYSIS_REPAIR_USER_PROMPT_PATH = SCRIPT_DIR / "script_induction_analysis_repair_user_llm.txt"
ITEM_SYSTEM_PROMPT_PATH = SCRIPT_DIR / "script_item_extraction_system.txt"
ITEM_USER_PROMPT_PATH = SCRIPT_DIR / "script_item_extraction_user.txt"
ITEM_REPAIR_USER_PROMPT_PATH = SCRIPT_DIR / "script_item_repair_user.txt"
JSON_PARSE_REPAIR_SYSTEM_PROMPT_PATH = SCRIPT_DIR / "json_parse_repair_system.txt"
JSON_PARSE_REPAIR_USER_PROMPT_PATH = SCRIPT_DIR / "json_parse_repair_user.txt"


def sanitize_agent_log(raw_log: str) -> str:
    """Remove markdown logger header/trailer (same as error_analysis_agent)."""
    text = raw_log.lstrip()
    text = re.compile(
        r"^# Chat History[^\n]*\n\n\*\*Timestamp\*\*:[^\n]*\n\n---\n\n", re.MULTILINE
    ).sub("", text)
    idx = text.find("## RESULT")
    if idx != -1:
        text = text[:idx].rstrip()
    return re.sub(r"\n---\s*$", "", text).rstrip()


def list_existing_scripts(skill_dir: str) -> str:
    p = Path(skill_dir)
    if not p.is_dir():
        return "(skill directory not found)"
    scripts = sorted(f.name for f in p.glob("*.py"))
    return "\n".join(f"- {s}" for s in scripts) if scripts else "(no scripts yet)"


def _create_client(
    model: str,
    base_url: str | None,
    api_key: str | None,
    generation_config: dict | None,
    cache_path: str | None = None,
) -> OpenAIClient:
    return OpenAIClient(
        model=model,
        api_key=api_key or os.getenv("OPENAI_API_KEY") or "EMPTY",
        base_url=base_url or os.getenv("OPENAI_BASE_URL"),
        cache_path=cache_path,
        generation_config=generation_config,
    )


def _render_prompt(template_path: Path, **kwargs: str) -> str:
    text = template_path.read_text(encoding="utf-8")
    for key, value in kwargs.items():
        text = text.replace(f"{{{key}}}", value)
    return text


def _copy_tree_contents(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        target = dst / item.name
        if item.is_dir():
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(item, target)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)


def _find_agent_work_workbook(induction_path: Path, kind: str) -> Path | None:
    agent_work = induction_path / "agent_work"
    preferred: list[str] = []
    if kind == "input":
        preferred = ["input.xlsx", "input.xlsm", "input.xls"]
    elif kind == "output":
        preferred = ["output.xlsx", "output.xlsm", "output.xls"]
    for name in preferred:
        candidate = agent_work / name
        if candidate.exists():
            return candidate
    for path in sorted(agent_work.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".xlsx", ".xlsm", ".xls"}:
            continue
        if kind in path.stem.lower():
            return path
    return None


def _extract_task_field(agent_log: str, field: str) -> str | None:
    match = re.search(rf"^- {re.escape(field)}:\s*(.+)$", agent_log, re.MULTILINE)
    if not match:
        return None
    value = match.group(1).strip()
    return value if value else None


def _render_item_context(item: dict[str, Any]) -> str:
    return json.dumps(item, indent=2, ensure_ascii=False)


def _run_plain_markdown_analysis(
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    base_url: str | None,
    api_key: str | None,
    generation_config: dict | None,
    cache_path: str | None,
    parse_retries: int,
) -> tuple[dict[str, Any], str]:
    client = _create_client(model, base_url, api_key, generation_config, cache_path=cache_path)
    messages = [
        Message(role="system", content=system_prompt),
        Message(role="user", content=user_prompt),
    ]
    reply = client.chat(messages)
    parsed: dict[str, Any] | None = None
    last_error: str | None = None

    for _ in range(parse_retries + 1):
        try:
            parsed = parse_induction_markdown(reply)
            break
        except ArtifactValidationError as exc:
            last_error = str(exc)
            if _ >= parse_retries:
                break
            repair_prompt = _render_prompt(
                ANALYSIS_REPAIR_USER_PROMPT_PATH,
                parser_feedback=last_error,
                previous_output=reply,
            )
            messages.append(Message(role="assistant", content=reply))
            messages.append(Message(role="user", content=repair_prompt))
            reply = client.chat(messages)

    if parsed is None:
        raise ArtifactValidationError(last_error or "Could not parse induction markdown")
    return parsed, reply


def _run_react_json_agent(
    *,
    system_prompt: str,
    user_prompt: str,
    induction_dir: Path,
    model: str,
    max_turns: int,
    base_url: str | None,
    api_key: str | None,
    generation_config: dict | None,
    cache_path: str | None,
    verbose: bool,
    log_filename: str,
    session_name: str,
    result_tag: str,
    validator,
    parse_retries: int = 2,
    log_dir: Path | None = None,
) -> tuple[dict[str, Any], str]:
    bash_tool = create_bash_tool(working_dir=str(induction_dir))
    client = _create_client(model, base_url, api_key, generation_config, cache_path=cache_path)
    logger = ChatHistoryLogger(
        log_dir=str((log_dir or induction_dir)),
        format="markdown",
        log_filename=log_filename,
    )
    logger.start_session(session_name, user_prompt)
    logger.log_system_prompt(system_prompt)
    logger.log_user_task(f"Task: {user_prompt}")

    agent = ReActAgent(
        client=client,
        tools=[bash_tool],
        config=AgentConfig(max_turns=max_turns, system_template=system_prompt, verbose=verbose),
        on_step=lambda step: logger.log_step(step),
    )

    result = asyncio.run(agent.run_async(user_prompt))
    final_answer = result.final_answer or ""
    parsed: dict[str, Any] | None = None
    last_error: str | None = None

    for attempt in range(parse_retries + 1):
        try:
            parsed = validator(extract_tagged_json(final_answer, result_tag))
            break
        except (json.JSONDecodeError, ArtifactValidationError) as exc:
            last_error = str(exc)
            if attempt >= parse_retries or not result.success:
                break
            continuation = (
                _render_prompt(JSON_PARSE_REPAIR_SYSTEM_PROMPT_PATH, result_tag=result_tag)
                + "\n\n"
                + _render_prompt(
                    JSON_PARSE_REPAIR_USER_PROMPT_PATH,
                    result_tag=result_tag,
                    parser_feedback=last_error,
                )
            )
            result = agent.continue_with_message(continuation)
            final_answer = result.final_answer or ""

    logger.log_result(
        success=result.success and parsed is not None,
        answer=final_answer if parsed is not None else f"Parse failed: {last_error}\n\n{final_answer}",
        turns=result.total_turns,
        error=None if parsed is not None else last_error,
    )

    if parsed is None:
        raise ArtifactValidationError(last_error or f"Could not parse <{result_tag}> payload")
    return parsed, final_answer


def _materialize_spreadsheet_validator(induction_path: Path, test: dict[str, Any]) -> None:
    validator_path = (induction_path / str(test["validator_path"])).resolve()
    validators_root = (induction_path / "validators").resolve()
    if validators_root not in validator_path.parents:
        raise ArtifactValidationError(
            f"{test.get('test_id', 'unknown')}: validator_path must live under validators/"
        )
    validator_path.parent.mkdir(parents=True, exist_ok=True)
    config = test["validator_config"]
    script = f'''#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys

sys.path.insert(0, {json.dumps(str(REPO_ROOT))})
from evaluation_official import compare_workbooks


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate spreadsheet output against a reference workbook")
    parser.add_argument("--reference", required=True)
    parser.add_argument("--hypothesis", required=True)
    args = parser.parse_args()
    ok, msg = compare_workbooks(
        args.reference,
        args.hypothesis,
        {json.dumps(str(config.get("instruction_type", "")))},
        {json.dumps(str(config.get("answer_position", "")))},
    )
    if ok:
        print("PASS")
        return 0
    print(msg or "Workbook comparison failed", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
'''
    validator_path.write_text(script, encoding="utf-8")


def _materialize_validators(induction_path: Path, reality_suite: dict[str, Any]) -> None:
    for test in reality_suite.get("tests", []):
        if test.get("validator_type") != "spreadsheet_compare_workbooks":
            raise ArtifactValidationError(
                f"Unsupported validator_type: {test.get('validator_type')}"
            )
        _materialize_spreadsheet_validator(induction_path, test)


def _materialize_script_artifact(induction_path: Path, payload: dict[str, Any]) -> Path:
    item_id = payload["item_id"]
    if payload["item_type"] == "generalizable_script":
        script = payload["script_artifact"]
        script_dir = induction_path / "induced_scripts"
        script_dir.mkdir(parents=True, exist_ok=True)
        path = script_dir / f"{script['name']}.py"
        path.write_text(script["code"], encoding="utf-8")
        return path

    fix = payload["fix_artifact"]
    fix_dir = induction_path / "proposed_script_fixes"
    fix_dir.mkdir(parents=True, exist_ok=True)
    path = fix_dir / fix["target_script"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(fix["fixed_code"], encoding="utf-8")
    return path


def _build_default_reality_context(induction_path: Path, agent_log: str, item_id: str) -> dict[str, Any]:
    input_src = _find_agent_work_workbook(induction_path, "input")
    output_src = _find_agent_work_workbook(induction_path, "output")
    instruction_type = _extract_task_field(agent_log, "instruction_type") or ""
    answer_position = _extract_task_field(agent_log, "answer_position") or ""

    context: dict[str, Any] = {
        "observed_input_path": None,
        "observed_output_path": None,
        "instruction_type": instruction_type,
        "answer_position": answer_position,
    }
    if input_src is not None:
        context["observed_input_path"] = str(input_src.relative_to(induction_path))
        dst = induction_path / "test_inputs" / item_id / f"reality_01{input_src.suffix.lower()}"
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(input_src, dst)
        context["suggested_input_copy"] = str(dst.relative_to(induction_path))
    if output_src is not None:
        context["observed_output_path"] = str(output_src.relative_to(induction_path))
        dst = induction_path / "test_references" / item_id / f"reality_01_reference{output_src.suffix.lower()}"
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(output_src, dst)
        context["suggested_reference_copy"] = str(dst.relative_to(induction_path))
    return context


def _run_single_item(
    *,
    item: dict[str, Any],
    induction_path: Path,
    skill_dir: str,
    agent_log: str,
    model: str,
    item_max_turns: int,
    base_url: str | None,
    api_key: str | None,
    item_generation_config: dict | None,
    cache_path: str | None,
    verbose: bool,
    verification_repair_rounds: int,
    test_timeout_sec: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    item_id = item["item_id"]
    system_prompt = _render_prompt(
        ITEM_SYSTEM_PROMPT_PATH,
        working_directory=str(induction_path.resolve()),
        skill_dir=str(Path(skill_dir).resolve()),
    )
    user_prompt = _render_prompt(
        ITEM_USER_PROMPT_PATH,
        item_json=_render_item_context(item),
        agent_log=agent_log,
        working_dir=str(induction_path.resolve()),
        reality_context_json=json.dumps(
            _build_default_reality_context(induction_path, agent_log, item_id),
            indent=2,
            ensure_ascii=False,
        ),
    )

    payload, _ = _run_react_json_agent(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        induction_dir=induction_path,
        model=model,
        max_turns=item_max_turns,
        base_url=base_url,
        api_key=api_key,
        generation_config=item_generation_config,
        cache_path=cache_path,
        verbose=verbose,
        log_filename=f"item_extraction_{item_id}.md",
        session_name=f"script_item_extraction_{item_id}",
        result_tag=INDUCTION_TAG,
        validator=validate_item_extraction_payload,
        log_dir=induction_path,
    )

    attempts: list[dict[str, Any]] = []
    latest_payload = payload
    final_status = "failed_verification"
    for repair_round in range(verification_repair_rounds + 1):
        if latest_payload["item_id"] != item_id:
            raise ArtifactValidationError(f"{item_id}: payload item_id mismatch {latest_payload['item_id']!r}")
        try:
            _materialize_validators(induction_path, latest_payload["reality_suite"])
            validate_item_reality_suite_runtime(latest_payload["reality_suite"], induction_path)
            script_path = _materialize_script_artifact(induction_path, latest_payload)
            verification = run_item_verification(
                latest_payload["reality_suite"],
                induction_path,
                script_path,
                induction_path / "structured" / "verification_runs" / item_id / f"attempt_{repair_round + 1:02d}",
                test_timeout_sec,
            )
        except (ArtifactValidationError, TestExecutionError, Exception) as exc:
            verification = {
                "tests_run": 0,
                "tests_passed": 0,
                "tests_failed": 1,
                "overall_passed": False,
                "per_test_results": [],
                "failure_feedback_for_agent": str(exc),
            }
        verification["attempt_index"] = repair_round + 1
        attempts.append(verification)
        if verification["overall_passed"]:
            final_status = "passed"
            break
        if repair_round >= verification_repair_rounds:
            break

        repair_user = _render_prompt(
            ITEM_REPAIR_USER_PROMPT_PATH,
            item_json=_render_item_context(item),
            current_payload_json=json.dumps(latest_payload, indent=2, ensure_ascii=False),
            failure_feedback=verification["failure_feedback_for_agent"],
        )
        latest_payload, _ = _run_react_json_agent(
            system_prompt=system_prompt,
            user_prompt=repair_user,
            induction_dir=induction_path,
            model=model,
            max_turns=item_max_turns,
            base_url=base_url,
            api_key=api_key,
            generation_config=item_generation_config,
            cache_path=cache_path,
            verbose=verbose,
            log_filename=f"item_repair_{item_id}_round_{repair_round + 1:02d}.md",
            session_name=f"script_item_repair_{item_id}",
            result_tag=INDUCTION_TAG,
            validator=validate_item_extraction_payload,
            log_dir=induction_path,
        )

    return latest_payload, {
        "item_id": item_id,
        "status": final_status,
        "attempts": len(attempts),
        "tests_passed": attempts[-1]["tests_passed"] if attempts else 0,
        "tests_failed": attempts[-1]["tests_failed"] if attempts else 0,
        "history": attempts,
    }


def run_script_induction(
    induction_dir: str,
    agent_log_content: str,
    skill_dir: str,
    model: str,
    item_model: str | None = None,
    item_max_turns: int = 12,
    item_workers: int = 3,
    markdown_parse_retries: int = 2,
    verification_repair_rounds: int = 2,
    test_timeout_sec: int = 60,
    base_url: str | None = None,
    api_key: str | None = None,
    analysis_generation_config: dict | None = None,
    item_generation_config: dict | None = None,
    cache_dir: str | None = None,
    verbose: bool = True,
) -> dict[str, Any]:
    """Run the simplified script induction pipeline on a single instance."""
    induction_path = Path(induction_dir)
    structured_dir = induction_path / "structured"
    structured_dir.mkdir(parents=True, exist_ok=True)
    for dirname in (
        "test_inputs",
        "test_outputs",
        "test_references",
        "validators",
        "induced_scripts",
        "proposed_script_fixes",
    ):
        (induction_path / dirname).mkdir(parents=True, exist_ok=True)

    log_text = sanitize_agent_log(agent_log_content)
    existing = list_existing_scripts(skill_dir)
    context = {
        "induction_dir": str(induction_path),
        "skill_dir": str(Path(skill_dir).resolve()),
        "agent_work_files": sorted(
            str(p.relative_to(induction_path))
            for p in induction_path.rglob("*")
            if p.is_file() and structured_dir not in p.parents
        ),
        "existing_skill_scripts": existing.splitlines() if existing else [],
    }
    save_json(structured_dir / "induction_context.json", context)
    cache_path = str(Path(cache_dir) / "openai_client_cache.diskcache") if cache_dir else None

    analysis_system = _render_prompt(
        ANALYSIS_SYSTEM_PROMPT_PATH,
        working_directory=str(induction_path.resolve()),
        skill_dir=str(Path(skill_dir).resolve()),
    )
    analysis_user = _render_prompt(
        ANALYSIS_USER_PROMPT_PATH,
        agent_log=log_text,
        working_dir=str(induction_path.resolve()),
        existing_scripts=existing,
    )
    parsed_induction, induction_markdown = _run_plain_markdown_analysis(
        model=model,
        system_prompt=analysis_system,
        user_prompt=analysis_user,
        base_url=base_url,
        api_key=api_key,
        generation_config=analysis_generation_config,
        cache_path=cache_path,
        parse_retries=markdown_parse_retries,
    )
    (structured_dir / "induction_items.md").write_text(induction_markdown, encoding="utf-8")
    save_json(structured_dir / "generalizable_script_items.json", parsed_induction["generalizable_script_items"])
    save_json(structured_dir / "script_fix_items.json", parsed_induction["script_fix_items"])

    items = parsed_induction["items"]
    item_extractions: list[dict[str, Any]] = []
    verification_results: list[dict[str, Any]] = []
    resolved_item_model = item_model or model

    if items:
        max_workers = max(1, min(item_workers, len(items)))
        futures = {}
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            for item in items:
                futures[executor.submit(
                    _run_single_item,
                    item=item,
                    induction_path=induction_path,
                    skill_dir=skill_dir,
                    agent_log=log_text,
                    model=resolved_item_model,
                    item_max_turns=item_max_turns,
                    base_url=base_url,
                    api_key=api_key,
                    item_generation_config=item_generation_config,
                    cache_path=cache_path,
                    verbose=verbose,
                    verification_repair_rounds=verification_repair_rounds,
                    test_timeout_sec=test_timeout_sec,
                )] = item["item_id"]
            by_id: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
            for future in as_completed(futures):
                item_id = futures[future]
                try:
                    by_id[item_id] = future.result()
                except Exception as exc:
                    by_id[item_id] = (
                        {
                            "item_id": item_id,
                            "error": str(exc),
                        },
                        {
                            "item_id": item_id,
                            "status": "failed_extraction",
                            "attempts": 0,
                            "tests_passed": 0,
                            "tests_failed": 0,
                            "history": [],
                            "error": str(exc),
                        },
                    )
            for item in items:
                payload, verification = by_id[item["item_id"]]
                item_extractions.append(payload)
                verification_results.append(verification)
    save_json(structured_dir / "item_extractions.json", item_extractions)
    save_json(structured_dir / "item_verification_results.json", verification_results)

    summary_items = []
    for item in items:
        verification = next((v for v in verification_results if v["item_id"] == item["item_id"]), None)
        summary_items.append(
            {
                "item_id": item["item_id"],
                "item_type": item["item_type"],
                "title": item["title"],
                "status": verification["status"] if verification else "failed_extraction",
                "attempts": verification["attempts"] if verification else 0,
                "tests_passed": verification["tests_passed"] if verification else 0,
                "tests_failed": verification["tests_failed"] if verification else 0,
            }
        )
    overall_status = "passed" if summary_items and all(i["status"] == "passed" for i in summary_items) else (
        "empty" if not summary_items else "partial"
    )
    summary = {
        "instance_id": induction_path.name,
        "items": summary_items,
        "overall_status": overall_status,
    }
    save_json(structured_dir / "induction_summary.json", summary)
    (induction_path / "script_induction_report.md").write_text(
        render_script_induction_report(
            instance_id=induction_path.name,
            induction_markdown=induction_markdown,
            items=items,
            item_extractions=item_extractions,
            verification_results=verification_results,
        ),
        encoding="utf-8",
    )
    return summary
