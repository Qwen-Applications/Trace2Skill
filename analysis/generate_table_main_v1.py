from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


SOURCE_TABLE_PATH = Path("parallel_skill_evolution_arxiv/tables/table_main.tex")
OUTPUT_TABLE_PATH = Path("parallel_skill_evolution_arxiv/tables/table_main_v1.tex")
_ROW_END = "\\\\"
_NUMERIC_RE = re.compile(r"-?\d+(?:\.\d+)?")
_ROW_TERMINATOR_RE = re.compile(r"\\\\(?:\[[^\]]+\])?$")
_MAX_COLOR_INTENSITY = 60


@dataclass(frozen=True)
class TableCell:
    value: float
    bold: bool


@dataclass(frozen=True)
class EvolvedRow:
    author: str
    mode: str
    label: str
    cells: tuple[TableCell, ...]


def _strip_outer_wrapper(cell: str) -> str:
    text = cell.strip()
    while True:
        match = re.fullmatch(r"\\[A-Za-z]+\{(.*)\}", text)
        if not match:
            return text
        text = match.group(1).strip()


def _iter_table_rows(table_text: str) -> list[str]:
    rows: list[str] = []
    buffer: list[str] = []
    for raw_line in table_text.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        buffer.append(stripped)
        if _ROW_TERMINATOR_RE.search(stripped):
            rows.append(" ".join(buffer))
            buffer.clear()
    return rows


def _extract_cell(cell: str) -> TableCell:
    text = cell.strip()
    bold = "\\textbf{" in text
    numeric_text = _strip_outer_wrapper(text)
    number = _NUMERIC_RE.search(numeric_text)
    if number is None:
        raise ValueError(f"missing numeric value in cell: {cell!r}")
    return TableCell(value=float(number.group(0)), bold=bold)


def _parse_numeric_row(row: str) -> tuple[TableCell, ...]:
    cells = [part.strip() for part in row.removesuffix(_ROW_END).split("&")[1:]]
    if len(cells) != 9:
        raise ValueError(f"expected 9 cells, found {len(cells)} in row: {row!r}")
    return tuple(_extract_cell(cell) for cell in cells)


def _extract_author(row: str) -> str:
    marker = r"\textit{Skill Author: "
    start = row.find(marker)
    if start == -1:
        raise ValueError(f"missing author marker in row: {row!r}")
    start += len(marker)
    end = row.find("}", start)
    if end == -1:
        raise ValueError(f"missing author closing brace in row: {row!r}")
    return row[start:end]


def _parse_table(text: str) -> tuple[dict[str, tuple[TableCell, ...]], list[EvolvedRow]]:
    references: dict[str, tuple[TableCell, ...]] = {}
    evolved_rows: list[EvolvedRow] = []
    current_author: str | None = None
    current_mode: str | None = None

    for row in _iter_table_rows(text):
        if row.startswith(r"\quad No Skill"):
            references["No Skill"] = _parse_numeric_row(row)
            continue
        if row.startswith(r"\quad Human-Written"):
            references["Human-Written"] = _parse_numeric_row(row)
            continue
        if row.startswith(r"\quad Parametric"):
            references["Parametric"] = _parse_numeric_row(row)
            continue
        if r"\textit{Skill Author:" in row:
            current_author = _extract_author(row)
            continue
        if "Deepening (init: Human-Written)" in row:
            current_mode = "Deepening"
            continue
        if "Creation (init: Parametric)" in row:
            current_mode = "Creation"
            continue
        if not row.startswith(r"\quad\quad +"):
            continue
        if current_author is None or current_mode is None:
            raise ValueError(f"missing context for evolved row: {row!r}")
        label, *_ = [part.strip() for part in row.removesuffix(_ROW_END).split("&")]
        evolved_rows.append(
            EvolvedRow(
                author=current_author,
                mode=current_mode,
                label=label,
                cells=_parse_numeric_row(row),
            )
        )

    missing = {"No Skill", "Human-Written", "Parametric"} - set(references)
    if missing:
        raise ValueError(f"missing reference rows: {sorted(missing)}")
    return references, evolved_rows


def _format_absolute_cell(cell: TableCell) -> str:
    text = f"{cell.value:.2f}"
    return rf"\textbf{{{text}}}" if cell.bold else text


def _compute_column_scales(references: dict[str, tuple[TableCell, ...]], evolved_rows: list[EvolvedRow]) -> list[float]:
    maxima = [0.0] * 9
    for row in evolved_rows:
        baseline_name = "Human-Written" if row.mode == "Deepening" else "Parametric"
        baseline = references[baseline_name]
        for index, cell in enumerate(row.cells):
            maxima[index] = max(maxima[index], abs(cell.value - baseline[index].value))
    return maxima


def _format_delta_cell(cell: TableCell, baseline: TableCell, scale: float) -> str:
    delta = cell.value - baseline.value
    if abs(delta) < 1e-9:
        text = "0.00"
        return rf"\textbf{{{text}}}" if cell.bold else text

    intensity = _MAX_COLOR_INTENSITY if scale <= 0 else round(abs(delta) / scale * _MAX_COLOR_INTENSITY)
    intensity = max(1, min(_MAX_COLOR_INTENSITY, intensity))
    color = "green" if delta > 0 else "red"
    text = f"{delta:+.2f}"
    if cell.bold:
        text = rf"\textbf{{{text}}}"
    return rf"\cellcolor{{{color}!{intensity}}}{text}"


def generate_table_main_v1_text(source_path: Path = SOURCE_TABLE_PATH) -> str:
    references, evolved_rows = _parse_table(source_path.read_text(encoding="utf-8"))
    scales = _compute_column_scales(references, evolved_rows)

    lines = [
        r"\begin{table*}[t]",
        r"\centering",
        r"\setlength{\tabcolsep}{3.5pt}",
        r"\caption{%",
        r"    Main results shown as deltas from the corresponding baseline (\%).",
        r"    \textbf{Skill Author} = model that evolved the skill (row groups);",
        r"    \textbf{Skill User} = model at inference (column groups).",
        r"    Reference rows remain absolute scores for context.",
        r"    Evolved rows show signed deltas with per-column \texttt{xcolor} intensity;",
        r"    green = improvement, red = decline.",
        r"    Deepening vs.\ Human-Written; Creation vs.\ Parametric.",
        r"    \textbf{Avg}: same metric as Table~\ref{tab:main}, now expressed as delta from the corresponding baseline.%",
        r"}",
        r"\label{tab:main_v1}",
        r"\begin{tabular}{@{}l cccc cccc c@{}}",
        r"\toprule",
        r"& \multicolumn{4}{c}{\textit{Skill User: Qwen3.5-122B-A10B}}",
        r"& \multicolumn{4}{c}{\textit{Skill User: Qwen3.5-35B-A3B}}",
        r"& \\",
        r"\cmidrule(lr){2-5}\cmidrule(lr){6-9}",
        r"& \multicolumn{3}{c}{\textit{SpreadsheetBench}} & \multicolumn{1}{c}{\textit{OOD}}",
        r"& \multicolumn{3}{c}{\textit{SpreadsheetBench}} & \multicolumn{1}{c}{\textit{OOD}}",
        r"& \\",
        r"\cmidrule(lr){2-4}\cmidrule(lr){5-5}\cmidrule(lr){6-8}\cmidrule(lr){9-9}",
        r"\textbf{Condition}",
        r"    & \textbf{Vrf}$\uparrow$ & \textbf{Soft}$\uparrow$ & \textbf{Hard}$\uparrow$ & \textbf{WikiTQ}$\uparrow$",
        r"    & \textbf{Vrf}$\uparrow$ & \textbf{Soft}$\uparrow$ & \textbf{Hard}$\uparrow$ & \textbf{WikiTQ}$\uparrow$",
        r"    & \textbf{Avg}$\uparrow$ \\",
        r"\midrule",
        r"\multicolumn{10}{l}{\textit{Reference (absolute scores)}} \\",
    ]

    for name in ("No Skill", "Human-Written", "Parametric"):
        cells = " & ".join(_format_absolute_cell(cell) for cell in references[name])
        lines.append(rf"\quad {name}")
        lines.append(rf"    & {cells} \\")

    current_author = None
    current_mode = None
    lines.append(r"\midrule")
    for row in evolved_rows:
        if row.author != current_author:
            if current_author is not None:
                lines.append(r"\midrule")
            lines.append(rf"\multicolumn{{10}}{{l}}{{\textit{{Skill Author: {row.author}}}}} \\[2pt]")
            current_author = row.author
            current_mode = None
        if row.mode != current_mode:
            header = r"\quad\textit{Deepening (init: Human-Written)}" if row.mode == "Deepening" else r"\quad\textit{Creation (init: Parametric)}"
            lines.append(rf"\multicolumn{{10}}{{l}}{{{header}}} \\")
            current_mode = row.mode
        baseline_name = "Human-Written" if row.mode == "Deepening" else "Parametric"
        baseline = references[baseline_name]
        rendered = " & ".join(
            _format_delta_cell(cell, baseline[index], scales[index]) for index, cell in enumerate(row.cells)
        )
        suffix = r" \\[2pt]" if row.label == r"\quad\quad +Combined" else r" \\"
        lines.append(rf"{row.label}")
        lines.append(rf"    & {rendered}{suffix}")

    lines.extend([r"\bottomrule", r"\end{tabular}", r"\end{table*}"])
    return "\n".join(lines) + "\n"


def write_table_main_v1(source_path: Path = SOURCE_TABLE_PATH, output_path: Path = OUTPUT_TABLE_PATH) -> Path:
    output_path.write_text(generate_table_main_v1_text(source_path), encoding="utf-8")
    return output_path


def main() -> None:
    write_table_main_v1()


if __name__ == "__main__":
    main()
