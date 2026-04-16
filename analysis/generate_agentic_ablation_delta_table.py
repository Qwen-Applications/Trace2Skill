from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


SOURCE_TABLE_PATH = Path("parallel_skill_evolution_arxiv/tables/table_agentic_ablation_source.tex")
OUTPUT_TABLE_PATH = Path("parallel_skill_evolution_arxiv/tables/table_agentic_ablation.tex")
ROW_TERMINATOR_RE = re.compile(r"\\\\(?:\[[^\]]+\])?$")
MAX_COLOR_INTENSITY = 60
POSITIVE_COLOR = "Green3"
NEGATIVE_COLOR = "Red1"
NUMERIC_RE = re.compile(r"-?\d+(?:\.\d+)?")


@dataclass(frozen=True)
class Cell:
    value: float
    bold: bool
    same_model: bool


@dataclass(frozen=True)
class DataRow:
    label: str
    cells: tuple[Cell, ...]


def _iter_rows(text: str) -> list[str]:
    rows: list[str] = []
    buffer: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        buffer.append(stripped)
        if ROW_TERMINATOR_RE.search(stripped):
            rows.append(" ".join(buffer))
            buffer.clear()
    return rows


def _normalized_rows(text: str) -> list[str]:
    rows: list[str] = []
    for row in _iter_rows(text):
        if row.startswith(r"\midrule") and r"\multicolumn{10}{l}{\textit{Skill Author:" in row:
            rows.append(r"\midrule")
            author_start = row.find(r"\multicolumn{10}{l}{\textit{Skill Author:")
            rows.append(row[author_start:].strip())
            continue
        rows.append(row)
    return rows


def _extract_cell(cell_text: str) -> Cell:
    same_model = r"\samemodel{" in cell_text
    bold = r"\textbf{" in cell_text
    match = NUMERIC_RE.search(cell_text)
    if match is None:
        raise ValueError(f"missing numeric value in cell: {cell_text!r}")
    return Cell(value=float(match.group(0)), bold=bold, same_model=same_model)


def _parse_data_row(row: str) -> DataRow:
    parts = [part.strip() for part in row.removesuffix("\\\\").split("&")]
    label, cell_parts = parts[0], parts[1:]
    if len(cell_parts) != 9:
        raise ValueError(f"expected 9 data cells, found {len(cell_parts)} in row: {row!r}")
    return DataRow(label=label, cells=tuple(_extract_cell(part) for part in cell_parts))


def _format_absolute_cell(cell: Cell) -> str:
    text = f"{cell.value:.2f}"
    if cell.bold:
        text = rf"\textbf{{{text}}}"
    if cell.same_model:
        text = rf"\samemodel{{{text}}}"
    return text


def _format_delta_cell(cell: Cell, baseline: Cell, scale: float) -> str:
    delta = cell.value - baseline.value
    if abs(delta) < 1e-12:
        text = "0.00"
    else:
        intensity = round(abs(delta) / scale * MAX_COLOR_INTENSITY) if scale > 0 else MAX_COLOR_INTENSITY
        intensity = max(1, min(MAX_COLOR_INTENSITY, intensity))
        color = POSITIVE_COLOR if delta > 0 else NEGATIVE_COLOR
        text = rf"\cellcolor{{{color}!{intensity}}}{delta:+.2f}"
    if cell.bold:
        text = text.replace(f"{delta:+.2f}" if abs(delta) >= 1e-12 else "0.00", rf"\textbf{{{delta:+.2f}}}" if abs(delta) >= 1e-12 else r"\textbf{0.00}")
    if cell.same_model:
        text = rf"\samemodel{{{text}}}"
    return text


def generate_agentic_ablation_delta_text(source_path: Path = SOURCE_TABLE_PATH) -> str:
    source_rows = _normalized_rows(source_path.read_text(encoding="utf-8"))
    block_pairs: list[tuple[DataRow, DataRow]] = []
    pending_ours_for_scale: DataRow | None = None
    for row in source_rows:
        if row.startswith(r"\quad +Error (ours)"):
            pending_ours_for_scale = _parse_data_row(row)
            continue
        if row.startswith(r"\quad +Error LLM"):
            if pending_ours_for_scale is None:
                raise ValueError("encountered +Error LLM row before +Error (ours) row during scale computation")
            block_pairs.append((pending_ours_for_scale, _parse_data_row(row)))
            pending_ours_for_scale = None

    column_scales = [
        max(abs(ours.cells[i].value - llm.cells[i].value) for ours, llm in block_pairs) for i in range(9)
    ]

    lines: list[str] = [
        r"\centering",
        r"\small",
        r"\setlength{\tabcolsep}{3.5pt}",
        r"\caption{%",
        r"    Agentic error analysis (+Error, ours) shown as deltas against single-LLM-call error analysis (+Error~LLM) across all Author--Mode combinations (\%).",
        r"    Reference rows (+Error~LLM) remain absolute; +Error (ours) uses per-column delta heatmaps with \cellcolor{Green3!30} gains and \cellcolor{Red1!30} declines.",
        r"    Same-model cells retain \samemodel{gray shading}; \textbf{bold} marks the better absolute score within each pair.",
        r"}",
        r"\label{tab:agentic_ablation}",
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
        r"    & \textbf{Vrf} & \textbf{Soft} & \textbf{Hard} & \textbf{WikiTQ}",
        r"    & \textbf{Vrf} & \textbf{Soft} & \textbf{Hard} & \textbf{WikiTQ}",
        r"    & \textbf{Avg} \\",
    ]

    current_mode = None
    pending_ours_row: DataRow | None = None

    for row in source_rows:
        if row.startswith(r"\multicolumn{10}{l}{\textit{Skill Author:"):
            lines.append(row)
            current_mode = None
            pending_ours_row = None
            continue
        if row.startswith(r"\multicolumn{10}{l}{\quad\textit{Deepening"):
            current_mode = row
            lines.append(row)
            pending_ours_row = None
            continue
        if row.startswith(r"\multicolumn{10}{l}{\quad\textit{Creation"):
            current_mode = row
            lines.append(row)
            pending_ours_row = None
            continue
        if row.startswith(r"\quad +Error LLM"):
            llm_row = _parse_data_row(row)
            lines.append(row)
            if pending_ours_row is None:
                raise ValueError("encountered +Error LLM row before +Error (ours) row")
            ours_row = pending_ours_row
            rendered = " & ".join(
                _format_delta_cell(ours_row.cells[i], llm_row.cells[i], column_scales[i]) for i in range(len(ours_row.cells))
            )
            suffix = r" \\[2pt]" if current_mode and "Creation" in current_mode else r" \\"
            lines.append(r"\quad +Error (ours)")
            lines.append(rf"    & {rendered}{suffix}")
            pending_ours_row = None
            continue
        if row.startswith(r"\quad +Error (ours)"):
            pending_ours_row = _parse_data_row(row)
            continue
        if row == r"\midrule" or row == r"\bottomrule" or row.startswith(r"%%"):
            lines.append(row)

    if pending_ours_row is not None:
        raise ValueError("missing +Error LLM row for final +Error (ours) block")

    if lines[-1] != r"\bottomrule":
        lines.append(r"\bottomrule")
    lines.append(r"\end{tabular}")
    return "\n".join(lines) + "\n"


def write_agentic_ablation_delta(output_path: Path = OUTPUT_TABLE_PATH) -> Path:
    output_path.write_text(generate_agentic_ablation_delta_text(), encoding="utf-8")
    return output_path


def main() -> None:
    write_agentic_ablation_delta()


if __name__ == "__main__":
    main()
