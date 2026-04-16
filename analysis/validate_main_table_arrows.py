from __future__ import annotations

from pathlib import Path
import re


TABLE_PATH = Path("parallel_skill_evolution_arxiv/tables/table_main.tex")

_ROW_END = "\\\\"
_NUMERIC_RE = re.compile(r"-?\d+(?:\.\d+)?")
_MARKER_RE = re.compile(r"\\(good|bad)\{")


def _strip_outer_wrapper(cell: str) -> str:
    text = cell.strip()
    while True:
        match = re.fullmatch(r"\\[A-Za-z]+\{(.*)\}", text)
        if not match:
            return text
        text = match.group(1).strip()


def _extract_balanced_content(text: str, start: int) -> tuple[str, int]:
    depth = 1
    index = start
    while index < len(text):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:index], index + 1
        index += 1
    raise ValueError(f"unbalanced braces in cell: {text!r}")


def extract_marked_value(cell: str) -> tuple[str, float]:
    text = cell.strip()
    marker = _MARKER_RE.search(text)
    if marker is None:
        raise ValueError(f"missing arrow marker in cell: {cell!r}")

    direction = marker.group(1)
    inner, _ = _extract_balanced_content(text, marker.end())
    numeric_text = _strip_outer_wrapper(inner)
    number = _NUMERIC_RE.fullmatch(numeric_text)
    if number is None:
        raise ValueError(f"could not extract numeric value from cell: {cell!r}")
    return direction, float(number.group(0))


def _extract_numeric_value(cell: str) -> float:
    text = _strip_outer_wrapper(cell)
    number = _NUMERIC_RE.search(text)
    if number is None:
        raise ValueError(f"missing numeric value in cell: {cell!r}")
    return float(number.group(0))


def _parse_numeric_row(line: str) -> list[float]:
    cells = [part.strip() for part in line.split("&")[1:]]
    if len(cells) != 9:
        raise ValueError(f"expected 9 numeric cells, found {len(cells)} in row: {line!r}")
    return [_extract_numeric_value(cell) for cell in cells]


def _iter_table_rows(table_text: str) -> list[str]:
    rows: list[str] = []
    buffer: list[str] = []
    for raw_line in table_text.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        buffer.append(stripped)
        if stripped.endswith(_ROW_END):
            rows.append(" ".join(buffer))
            buffer.clear()
    return rows


def validate_table_main_arrow_directions(table_path_or_text: str | Path) -> list[str]:
    if isinstance(table_path_or_text, Path):
        table_text = table_path_or_text.read_text(encoding="utf-8")
    else:
        value = str(table_path_or_text)
        if "\n" in value or "\\" in value:
            table_text = value
        else:
            path = Path(value)
            table_text = path.read_text(encoding="utf-8") if path.exists() else value

    reference_rows: dict[str, list[float]] = {}
    mismatches: list[str] = []
    current_mode: str | None = None

    for row in _iter_table_rows(table_text):
        if row.startswith(r"\quad No Skill"):
            reference_rows["No Skill"] = _parse_numeric_row(row)
            continue
        if row.startswith(r"\quad Human-Written"):
            reference_rows["Human-Written"] = _parse_numeric_row(row)
            continue
        if row.startswith(r"\quad Parametric"):
            reference_rows["Parametric"] = _parse_numeric_row(row)
            continue
        if "Deepening (init: Human-Written)" in row:
            current_mode = "Deepening"
            continue
        if "Creation (init: Parametric)" in row:
            current_mode = "Creation"
            continue
        if not row.startswith(r"\quad\quad +"):
            continue

        if current_mode is None:
            raise ValueError(f"encountered evolved row before mode header: {row!r}")

        baseline_name = "Human-Written" if current_mode == "Deepening" else "No Skill"
        baseline = reference_rows.get(baseline_name)
        if baseline is None:
            raise ValueError(f"missing baseline row {baseline_name!r}")

        label, *cells = [part.strip() for part in row.removesuffix(_ROW_END).split("&")]
        if len(cells) != 9:
            raise ValueError(f"expected 9 evolved cells, found {len(cells)} in row: {row!r}")

        for index, cell in enumerate(cells):
            value = _extract_numeric_value(cell)
            baseline_value = baseline[index]
            expected = "good" if value > baseline_value else "bad" if value < baseline_value else "equal"
            has_marker = _MARKER_RE.search(cell) is not None

            if expected == "equal":
                if has_marker:
                    mismatches.append(
                        f"{label} col {index + 1}: value {value:.2f} equals baseline {baseline_value:.2f}; arrow is ambiguous"
                    )
                continue

            if not has_marker:
                mismatches.append(
                    f"{label} col {index + 1}: value {value:.2f} vs baseline {baseline_value:.2f}, "
                    f"expected {expected} but found no arrow"
                )
                continue

            direction, _ = extract_marked_value(cell)
            if direction != expected:
                mismatches.append(
                    f"{label} col {index + 1}: value {value:.2f} vs baseline {baseline_value:.2f}, "
                    f"expected {expected} but found {direction}"
                )

    missing_references = {"No Skill", "Human-Written", "Parametric"} - reference_rows.keys()
    if missing_references:
        raise ValueError(f"missing reference rows: {sorted(missing_references)}")

    return mismatches
