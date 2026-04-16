from __future__ import annotations

from pathlib import Path

try:
    from analysis.generate_agentic_ablation_delta_table import (
        SOURCE_TABLE_PATH,
        _normalized_rows,
        _parse_data_row,
    )
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from generate_agentic_ablation_delta_table import (  # type: ignore
        SOURCE_TABLE_PATH,
        _normalized_rows,
        _parse_data_row,
    )


OUTPUT_TABLE_PATH = Path("parallel_skill_evolution_arxiv/tables/table_agentic_ablation_v1.tex")


def _plain_text(value: float, bold: bool) -> str:
    text = f"{value:.2f}"
    return rf"\textbf{{{text}}}" if bold else text


def _plain_delta(value: float, baseline: float, bold: bool) -> str:
    delta = value - baseline
    text = f"{delta:+.2f}" if abs(delta) >= 1e-12 else "0.00"
    return rf"\textbf{{{text}}}" if bold else text


def generate_agentic_ablation_v1_text(source_path: Path = SOURCE_TABLE_PATH) -> str:
    source_rows = _normalized_rows(source_path.read_text(encoding="utf-8"))
    lines: list[str] = [
        r"\centering",
        r"\small",
        r"\setlength{\tabcolsep}{3.5pt}",
        r"\caption{%",
        r"    Agentic error analysis (+Error, ours) shown as signed deltas against single-LLM-call error analysis (+Error~LLM) across all Author--Mode combinations (\%).",
        r"    Reference rows (+Error~LLM) remain absolute; +Error (ours) is reported as plain deltas without color encoding.",
        r"}",
        r"\label{tab:agentic_ablation_v1}",
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
    pending_ours_row = None

    for row in source_rows:
        if row.startswith(r"\multicolumn{10}{l}{\textit{Skill Author:") or row == r"\midrule":
            lines.append(row)
            current_mode = None if row == r"\midrule" else current_mode
            pending_ours_row = None
            continue
        if row.startswith(r"\multicolumn{10}{l}{\quad\textit{Deepening") or row.startswith(
            r"\multicolumn{10}{l}{\quad\textit{Creation"
        ):
            current_mode = row
            lines.append(row)
            pending_ours_row = None
            continue
        if row.startswith(r"\quad +Error (ours)"):
            pending_ours_row = _parse_data_row(row)
            continue
        if row.startswith(r"\quad +Error LLM"):
            llm_row = _parse_data_row(row)
            lines.append(r"\quad +Error LLM")
            lines.append(
                "    & " + " & ".join(_plain_text(cell.value, cell.bold) for cell in llm_row.cells) + (r" \\[2pt]" if current_mode and "Deepening" in current_mode else r" \\")
            )
            if pending_ours_row is None:
                raise ValueError("encountered +Error LLM row before +Error (ours) row")
            ours_row = pending_ours_row
            lines.append(r"\quad +Error (ours)")
            lines.append(
                "    & "
                + " & ".join(
                    _plain_delta(ours_row.cells[i].value, llm_row.cells[i].value, ours_row.cells[i].bold)
                    for i in range(len(ours_row.cells))
                )
                + (r" \\[2pt]" if current_mode and "Creation" in current_mode else r" \\")
            )
            pending_ours_row = None
            continue
        if row == r"\bottomrule":
            lines.append(row)

    lines.append(r"\end{tabular}")
    return "\n".join(lines) + "\n"


def write_agentic_ablation_v1(output_path: Path = OUTPUT_TABLE_PATH) -> Path:
    output_path.write_text(generate_agentic_ablation_v1_text(), encoding="utf-8")
    return output_path


def main() -> None:
    write_agentic_ablation_v1()


if __name__ == "__main__":
    main()
