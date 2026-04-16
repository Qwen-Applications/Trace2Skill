from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


TABLE_MATH_PATH = Path("parallel_skill_evolution_arxiv/tables/table_math.tex")
TABLE_VQA_PATH = Path("parallel_skill_evolution_arxiv/tables/table_vqa.tex")
MAX_COLOR_INTENSITY = 60
POSITIVE_COLOR = "Green3"
NEGATIVE_COLOR = "Red1"


@dataclass(frozen=True)
class Cell:
    value: float
    bold: bool = False


def _format_value(value: float, decimals: int) -> str:
    return f"{value:.{decimals}f}"


def _format_absolute_cell(cell: Cell, decimals: int) -> str:
    text = _format_value(cell.value, decimals)
    return rf"\textbf{{{text}}}" if cell.bold else text


def _format_delta_cell(cell: Cell, baseline: Cell, scale: float, decimals: int) -> str:
    delta = cell.value - baseline.value
    if abs(delta) < 1e-12:
        text = _format_value(0.0, decimals)
        return rf"\textbf{{{text}}}" if cell.bold else text

    intensity = round(abs(delta) / scale * MAX_COLOR_INTENSITY) if scale > 0 else MAX_COLOR_INTENSITY
    intensity = max(1, min(MAX_COLOR_INTENSITY, intensity))
    color = POSITIVE_COLOR if delta > 0 else NEGATIVE_COLOR
    text = f"{delta:+.{decimals}f}"
    if cell.bold:
        text = rf"\textbf{{{text}}}"
    return rf"\cellcolor{{{color}!{intensity}}}{text}"


def generate_table_math_text() -> str:
    baseline = [Cell(92.0), Cell(90.4), Cell(89.0), Cell(83.3)]
    rows = [
        ("122B-Authored +Error", [Cell(95.0, True), Cell(93.3, True), Cell(94.0, True), Cell(88.3, True)]),
        ("35B-Authored +Error", [Cell(94.0, True), Cell(91.7, True), Cell(93.0, True), Cell(83.8, True)]),
    ]
    decimals = [1, 1, 1, 1]
    scales = [max(abs(cells[i].value - baseline[i].value) for _, cells in rows) for i in range(4)]

    lines = [
        r"\centering",
        r"\small",
        r"\setlength{\tabcolsep}{4pt}",
        r"\caption{%",
        r"    Math reasoning results shown as deltas from the No Skill baseline.",
        r"    \textbf{D-Test}: DAPO-Math-Test-100 pass rate (\%);",
        r"    \textbf{AIME}: AIME~2026 avg@8 over 30 problems (\%).",
        rf"    Reference row remains absolute; evolved rows use \cellcolor{{{POSITIVE_COLOR}!30}} green / \cellcolor{{{NEGATIVE_COLOR}!30}} red delta intensity.",
        r"}",
        r"\label{tab:math}",
        r"\begin{tabular}{@{}l cccc@{}}",
        r"\toprule",
        r"& \multicolumn{2}{c}{\textit{Skill User: 122B}}",
        r"& \multicolumn{2}{c}{\textit{Skill User: 35B}} \\",
        r"\cmidrule(lr){2-3}\cmidrule(lr){4-5}",
        r"\textbf{Condition} & \textbf{D-Test}$\uparrow$ & \textbf{AIME}$\uparrow$ & \textbf{D-Test}$\uparrow$ & \textbf{AIME}$\uparrow$ \\",
        r"\midrule",
        rf"\quad No Skill              & {_format_absolute_cell(baseline[0], 1)} & {_format_absolute_cell(baseline[1], 1)} & {_format_absolute_cell(baseline[2], 1)} & {_format_absolute_cell(baseline[3], 1)} \\",
    ]
    for label, cells in rows:
        rendered = " & ".join(_format_delta_cell(cells[i], baseline[i], scales[i], decimals[i]) for i in range(4))
        lines.append(rf"\quad {label:<21} & {rendered} \\")
    lines.extend([r"\bottomrule", r"\end{tabular}"])
    return "\n".join(lines) + "\n"


def generate_table_vqa_text() -> str:
    baseline = [Cell(0.6424), Cell(71.2), Cell(0.6843), Cell(75.2)]
    rows = [
        ("Skill Author: Qwen3.5-122B-A10B", "+Error", [Cell(0.8063, True), Cell(86.5, True), Cell(0.8397, True), Cell(88.8, True)]),
        ("Skill Author: Qwen3.5-35B-A3B", "+Error", [Cell(0.6517), Cell(72.1), Cell(0.6223), Cell(69.0)]),
    ]
    decimals = [4, 1, 4, 1]
    scales = [max(abs(cells[i].value - baseline[i].value) for _, _, cells in rows) for i in range(4)]

    lines = [
        r"\centering",
        r"\small",
        r"\setlength{\tabcolsep}{4pt}",
        r"\caption{%",
        r"    DocVQA results shown as deltas from the No Skill baseline (evaluation set: 2{,}649 instances).",
        r"    \textbf{ANLS}: Average Normalized Levenshtein Similarity;",
        r"    \textbf{Acc}: ANLS~$\geq 0.5$ (\%).",
        rf"    Reference rows remain absolute; evolved rows use \cellcolor{{{POSITIVE_COLOR}!30}} green / \cellcolor{{{NEGATIVE_COLOR}!30}} red delta intensity.",
        r"}",
        r"\label{tab:vqa}",
        r"\begin{tabular}{@{}l cccc@{}}",
        r"\toprule",
        r"& \multicolumn{2}{c}{\textit{Skill User: 122B}}",
        r"& \multicolumn{2}{c}{\textit{Skill User: 35B}} \\",
        r"\cmidrule(lr){2-3}\cmidrule(lr){4-5}",
        r"\textbf{Condition} & \textbf{ANLS}$\uparrow$ & \textbf{Acc}$\uparrow$ & \textbf{ANLS}$\uparrow$ & \textbf{Acc}$\uparrow$ \\",
        r"\midrule",
    ]
    for author, label, cells in rows:
        lines.append(rf"\multicolumn{{5}}{{l}}{{\textit{{{author}}}}} \\")
        lines.append(
            rf"\quad No Skill    & {_format_absolute_cell(baseline[0], 4)} & {_format_absolute_cell(baseline[1], 1)} & {_format_absolute_cell(baseline[2], 4)} & {_format_absolute_cell(baseline[3], 1)} \\"
        )
        rendered = " & ".join(_format_delta_cell(cells[i], baseline[i], scales[i], decimals[i]) for i in range(4))
        lines.append(rf"\quad {label}    & {rendered} \\")
        if author != rows[-1][0]:
            lines.append(r"\midrule")
    lines.extend([r"\bottomrule", r"\end{tabular}"])
    return "\n".join(lines) + "\n"


def write_table_math(output_path: Path = TABLE_MATH_PATH) -> Path:
    output_path.write_text(generate_table_math_text(), encoding="utf-8")
    return output_path


def write_table_vqa(output_path: Path = TABLE_VQA_PATH) -> Path:
    output_path.write_text(generate_table_vqa_text(), encoding="utf-8")
    return output_path


def main() -> None:
    write_table_math()
    write_table_vqa()


if __name__ == "__main__":
    main()
