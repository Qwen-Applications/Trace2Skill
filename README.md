# Trace2Skill

Official code and released spreadsheet skills for:

**Trace2Skill: Distill Trajectory-Local Lessons into Transferable Agent Skills**  
Paper: <https://arxiv.org/html/2603.25158v3>

Trace2Skill is a framework for automatic skill adaptation and skill creation from agent execution traces. Instead of updating skills sequentially from individual trajectories, Trace2Skill analyzes a pool of traces in parallel, proposes trajectory-local patches with multiple analysts, and hierarchically consolidates them into a unified, conflict-free skill directory. The paper studies two evolution modes: `skill deepening` from an existing human-written skill, and `skill creation from scratch` from a weak initial draft.

## News

- `[2026/04/15]` Repository released.
- `[2026/03/26]` Paper released as a work in progress.

## Overview

Trace2Skill follows the three-stage pipeline described in the paper:

1. `Trajectory Generation`
   A frozen agent rolls out on a task pool and produces successful and failed trajectories.
2. `Parallel Multi-Agent Patch Proposal`
   Success analysts and error analysts process traces independently and propose skill patches.
3. `Conflict-Free Patch Consolidation`
   Proposed patches are merged hierarchically with programmatic conflict prevention and format validation.

The paper shows that this holistic, parallel consolidation strategy yields more transferable skills than both sequential online editing and retrieval-based experience-memory baselines. In addition to spreadsheet tasks, the paper also studies math reasoning and visual question answering.

## Released Skills

We release the top-performing spreadsheet skills referenced in the paper under `released_skills/`:

- `trace2skill-xlsx-35B-combined`
  Trace2Skill deepened `xlsx` from 35B trajectories in the combined analyst setting.
- `xlsx-35B`
  Trace2Skill created a spreadsheet skill from scratch in the 35B error setting.
- `trace2skill-xlsx-122B-combined`
  Trace2Skill deepened `xlsx` from 122B trajectories in the combined analyst setting.
- `xlsx-122B`
  Trace2Skill created a spreadsheet skill from scratch in the 122B error setting.

The runtime skill tree in `spreadsheet_agent/skills/` includes the released `xlsx-35B` and `xlsx-122B` variants directly. The full paper release set is preserved separately in `released_skills/`.

## What Is Included

- `run_spreadsheetbench.py`
  Runs SpreadsheetBench with the preloaded-skill spreadsheet-agent setup used in this release.
- `spreadsheet_agent/skills/`
  Includes the spreadsheet-agent skill tree used by the released benchmark setup.
- `released_skills/`
  Includes the four released paper skills listed above.
- `skill_evolver/`
  Includes the public parallel skill-evolution entrypoints and their direct support modules for Trace2Skill patch proposal and consolidation.
- `run_error_analysis.py` and `analysis/`
  Includes the error analysis, success analysis, parsing, and compression scripts used by the trajectory-to-skill workflow.
- `evaluate_outputs.py`
  Scores SpreadsheetBench outputs against the benchmark ground truth.

## Main Entry Points

```bash
python run_spreadsheetbench.py --data_path <dataset> --model <model>
python run_error_analysis.py --help
python analysis/run_error_analysis_llm.py --help
python analysis/run_success_analysis_llm.py --help
python -m skill_evolver.run_parallel_skill_evolution --help
python -m skill_evolver.run_parallel_success_skill_evolution --help
python -m skill_evolver.run_parallel_combined_skill_evolution --help
```

## Repository Scope

- The SpreadsheetBench runner in this repository supports the preloaded-skill spreadsheet-agent flow used in the released spreadsheet experiments.
- The paper release skill artifacts are included for inspection and reuse, not all of them are loaded simultaneously by the benchmark runner.

## Directory Sketch

```text
trace2skill/
├── analysis/
├── released_skills/
├── skill_evolver/
├── spreadsheet_agent/
├── src/react_agent/
├── evaluate_outputs.py
├── run_error_analysis.py
└── run_spreadsheetbench.py
```

## Notes

This repository focuses on the spreadsheet setting and released skills discussed in the paper, while keeping the core Trace2Skill evolution pipeline runnable.
