<div align="center">
<h1>Trace2Skill: Distill Trajectory-Local Lessons into Transferable Agent Skills</h1>

<!-- Badges -->
<a href="https://github.com/Qwen-Applications">
  <img src="https://img.shields.io/badge/Qwen-Applications-4433FF?style=for-the-badge" alt="Qwen Applications">
</a>
<a href="https://arxiv.org/abs/2603.25158">
  <img src="https://img.shields.io/badge/arXiv-2603.25158-b31b1b.svg?style=for-the-badge" alt="arXiv">
</a>
<a href="https://github.com/Qwen-Applications/Trace2Skill">
  <img src="https://img.shields.io/badge/Github-Code-black?style=for-the-badge&logo=github" alt="Github">
</a>
<a href="https://opensource.org/licenses/Apache-2.0">
  <img src="https://img.shields.io/badge/License-Apache%202.0-green.svg?style=for-the-badge" alt="License">
</a>

<p align="center">
  <i><b>Qwen Large Model Application Team, Alibaba</b></i>
</p>

In this project, we provide the official code and released spreadsheet skills for Trace2Skill. Trace2Skill automatically adapts and creates agent skills from execution traces. Instead of updating skills sequentially from individual trajectories, it analyzes a pool of traces in parallel, proposes trajectory-local patches with multiple analysts, and hierarchically consolidates them into a unified, conflict-free skill directory.

The paper studies two evolution modes: <b>skill deepening</b> from an existing human-written skill, and <b>skill creation from scratch</b> from a weak initial draft. In addition to spreadsheet tasks, the paper also studies math reasoning and visual question answering.

<p align="center">
  <b>Trace2Skill pipeline:</b> trajectory generation -> parallel multi-agent patch proposal -> conflict-free patch consolidation
</p>

</div>

## 1. Setup and Installation

Clone this repository and install the lightweight runtime dependencies:

```bash
git clone https://github.com/Qwen-Applications/Trace2Skill.git
cd Trace2Skill
python -m pip install openai tqdm openpyxl requests diskcache
```

The runners use OpenAI-compatible chat APIs by default. Set the API credentials for your provider:

```bash
export OPENAI_API_KEY=<your_api_key>
export OPENAI_BASE_URL=<optional_openai_compatible_endpoint>
```

For local OpenAI-compatible serving, pass `--api-key EMPTY` and `--base-url http://localhost:8000/v1` to the analysis or skill-evolution entrypoints.

## 2. Data and Released Skills

Prepare a SpreadsheetBench dataset directory or JSONL file and pass it with `--data_path` when running evaluation. The benchmark runner uses the preloaded spreadsheet skills under `spreadsheet_agent/skills/`.

We release the top-performing spreadsheet skills referenced in the paper under `released_skills/`:

| Skill | Setting | Source traces | Location |
|-------|---------|---------------|----------|
| `trace2skill-xlsx-35B-combined` | Self-deepen | 35B combined success/error traces | `released_skills/trace2skill-xlsx-35B-combined/` |
| `xlsx-35B` | Self-create | 35B error traces | `released_skills/xlsx-35B/` |
| `trace2skill-xlsx-122B-combined` | Self-deepen | 122B combined success/error traces | `released_skills/trace2skill-xlsx-122B-combined/` |
| `xlsx-122B` | Self-create | 122B error traces | `released_skills/xlsx-122B/` |

The runtime skill tree in `spreadsheet_agent/skills/` includes the released `xlsx-35B` and `xlsx-122B` variants directly. The full paper release set is preserved separately in `released_skills/`.

## 3. Running and Skill Evolution

From the repository root, run the SpreadsheetBench agent, evaluate outputs, analyze trajectories, and evolve skills with the following entrypoints:

| Workflow | Command | Output |
|----------|---------|--------|
| Run SpreadsheetBench | `python run_spreadsheetbench.py --data_path <dataset> --model <model>` | Spreadsheet outputs and optional trajectory logs |
| Evaluate outputs | `python evaluate_with_official.py --data_path <dataset> --output_dir <outputs>` | Official-compatible evaluation results |
| Match results and logs | `python analyze_results.py --help` | Failure triage records |
| Agentic error analysis | `python analysis/run_error_analysis.py --help` | `parsed_error_records.json` |
| Single-call success analysis | `python analysis/run_success_analysis_llm.py --help` | `parsed_success_records.json` |
| Parse error analysis | `python analysis/parse_error_analysis_outputs.py --help` | Parsed error JSON |
| Parse success analysis | `python analysis/parse_success_analysis_outputs.py --help` | Parsed success JSON |
| Parallel error-driven skill evolution | `python -m skill_evolver.run_parallel_skill_evolution --help` | Updated skill directory |
| Parallel combined skill evolution | `python -m skill_evolver.run_parallel_combined_skill_evolution --help` | Updated skill directory |

Example benchmark run:

```bash
python run_spreadsheetbench.py \
  --data_path <dataset> \
  --model <model> \
  --output_dir outputs/spreadsheetbench \
  --log_dir outputs/logs
```

Example parallel skill evolution from parsed error records:

```bash
python -m skill_evolver.run_parallel_skill_evolution \
  --input-json <analysis_output_or_parsed_error_records.json> \
  --skill-dir spreadsheet_agent/skills/xlsx/ \
  --model <model> \
  --max-workers 4 \
  --save-intermediates
```

The skill-evolver entrypoints accept either parsed JSON files or the corresponding analysis output directories directly.

## 4. Reproduction Note

The spreadsheet verified data for reproduction is included under `data/spreadsheetbench_verified/spreadsheetbench_verified_400`. The commands below cover baseline evaluation, error/success analysis, Trace2Skill evolution, and evolved-skill evaluation. Set `MODEL` to your OpenAI-compatible served model name or path. The commands use the Qwen reproduction configs in `gen_config/`; for Gemma runs, replace them with `gen_config/gemma4_instruct.json` and `gen_config/gemma4_thinking.json`.

Run the baseline spreadsheet agent on the training split (`0:200`) and produce the error/success analysis records:

```bash
DATA_PATH=data/spreadsheetbench_verified/spreadsheetbench_verified_400
MODEL=Qwen3.5-122B-A10B
WORKERS=128
BASELINE_DIR=outputs/reproduce/baseline_run
GENERATION_CONFIG=gen_config/qwen3.5_35B_122B_instruct_reasoning.json
THINK_GENERATION_CONFIG=gen_config/qwen3.5_35B_122B_thinking_reasoning.json

python run_spreadsheetbench.py \
  --data_path "$DATA_PATH" \
  --model "$MODEL" \
  --log_dir "$BASELINE_DIR/logs" \
  --log_format markdown \
  --working_dir "$BASELINE_DIR/work" \
  --output_dir "$BASELINE_DIR/outputs" \
  --max_turns 100 \
  --workers "$WORKERS" \
  --skills_dir spreadsheet_agent/skills \
  --generation_config "$GENERATION_CONFIG" \
  --start_idx 0 \
  --end_idx 200

python evaluate_with_official.py \
  --data_path "$DATA_PATH" \
  --output_dir "$BASELINE_DIR/outputs" \
  --verbose \
  --start_idx 0 \
  --end_idx 200

python analyze_results.py \
  --eval_results "$BASELINE_DIR/outputs/eval_official_results.json" \
  --log_dir "$BASELINE_DIR/logs"

python analysis/run_error_analysis.py \
  --data_path "$DATA_PATH" \
  --work_dir "$BASELINE_DIR/work" \
  --logs_dir "$BASELINE_DIR/logs" \
  --output_dir "$BASELINE_DIR/error_analysis" \
  --model "$MODEL" \
  --workers "$WORKERS" \
  --generation_config "$GENERATION_CONFIG" \
  --max_turns 100

python analysis/run_success_analysis_llm.py \
  --logs_dir "$BASELINE_DIR/logs" \
  --output_dir "$BASELINE_DIR/success_analysis" \
  --model "$MODEL" \
  --max_workers "$WORKERS" \
  --generation_config "$THINK_GENERATION_CONFIG"

python analysis/parse_error_analysis_outputs.py \
  --input_dir "$BASELINE_DIR/error_analysis" \
  --output "$BASELINE_DIR/error_analysis_parsed.json" 2>&1 \
  | tee "$BASELINE_DIR/error_analysis_parsed.log"

python analysis/parse_success_analysis_outputs.py \
  --input_dir "$BASELINE_DIR/success_analysis" \
  --output "$BASELINE_DIR/success_analysis_parsed.json" 2>&1 \
  | tee "$BASELINE_DIR/success_analysis_parsed.log"
```

Run Trace2Skill evolution from the parsed training-split error analysis records:

```bash
SEED=41
PATCH_FORMAT=json
EVOLVE_ROOT=outputs/reproduce/skill_evolution_seed_${SEED}
EVOLUTION_DIR="$EVOLVE_ROOT/error_driven_skill_evolution"
EVOLVED_RUN_DIR=outputs/reproduce/evolved_run_seed_${SEED}
EVOLVED_SKILLS="$EVOLUTION_DIR/skills"

mkdir -p "$EVOLVED_SKILLS"
cp -r spreadsheet_agent/skills/. "$EVOLVED_SKILLS"

python -m skill_evolver.run_parallel_skill_evolution \
  --input-json "$BASELINE_DIR/error_analysis_parsed.json" \
  --skill-dir "$EVOLVED_SKILLS/xlsx" \
  --model "$MODEL" \
  --verbose \
  --batch-size 1 \
  --changelog "$EVOLUTION_DIR/change.log" \
  --save-intermediates \
  --intermediates-dir "$EVOLUTION_DIR/intermediates" \
  --max-workers "$WORKERS" \
  --prompt generic \
  --generation-config "$THINK_GENERATION_CONFIG" \
  --parse-failure-dir "$EVOLUTION_DIR/parse_failures" \
  --patch-pipeline "$PATCH_FORMAT" \
  --seed "$SEED"
```

Because the pipeline is long and the ReAct agent can work up to 100 steps, exact 100% reproduction can be difficult even when all random seeds are controlled. The general trend and paper takeaways should still be reproducible. Also, because all skills are modified by LLMs, a single hallucinated edit can damage overall skill robustness. To reduce this risk, run the training-set validation below and verify that the Trace2Skill-evolved skill does not hurt training-set performance compared with the baseline skill before using it for final evaluation.

In the paper, we ran skill evolution plus this training-set validation with three random seeds, then selected the evolved skill with the best training-set validation performance before evaluating it on the held-out split.

For that training-set validation on the evolution split (`0:200`), compare the baseline and evolved skill `eval_official_results.json` summaries:

```bash
VALIDATION_DIR=outputs/reproduce/validation_train

python run_spreadsheetbench.py \
  --data_path "$DATA_PATH" \
  --model "$MODEL" \
  --log_dir "$VALIDATION_DIR/baseline_logs" \
  --working_dir "$VALIDATION_DIR/baseline_work" \
  --output_dir "$VALIDATION_DIR/baseline_outputs" \
  --max_turns 100 \
  --workers "$WORKERS" \
  --skills_dir spreadsheet_agent/skills \
  --seeds "$SEED" \
  --generation_config "$GENERATION_CONFIG" \
  --start_idx 0 \
  --end_idx 200

python evaluate_with_official.py \
  --data_path "$DATA_PATH" \
  --output_dir "$VALIDATION_DIR/baseline_outputs" \
  --start_idx 0 \
  --end_idx 200

python run_spreadsheetbench.py \
  --data_path "$DATA_PATH" \
  --model "$MODEL" \
  --log_dir "$VALIDATION_DIR/evolved_logs" \
  --working_dir "$VALIDATION_DIR/evolved_work" \
  --output_dir "$VALIDATION_DIR/evolved_outputs" \
  --max_turns 100 \
  --workers "$WORKERS" \
  --skills_dir "$EVOLVED_SKILLS" \
  --seeds "$SEED" \
  --generation_config "$GENERATION_CONFIG" \
  --start_idx 0 \
  --end_idx 200

python evaluate_with_official.py \
  --data_path "$DATA_PATH" \
  --output_dir "$VALIDATION_DIR/evolved_outputs" \
  --start_idx 0 \
  --end_idx 200
```

After selecting the best seed by training-set validation, evaluate that evolved skill on the held-out split (`200:400`):

```bash
python run_spreadsheetbench.py \
  --data_path "$DATA_PATH" \
  --model "$MODEL" \
  --log_dir "$EVOLVED_RUN_DIR/logs" \
  --log_format markdown \
  --working_dir "$EVOLVED_RUN_DIR/work" \
  --output_dir "$EVOLVED_RUN_DIR/outputs" \
  --max_turns 100 \
  --workers "$WORKERS" \
  --skills_dir "$EVOLVED_SKILLS" \
  --seeds "$SEED" \
  --generation_config "$GENERATION_CONFIG" \
  --start_idx 200 \
  --end_idx 400

python evaluate_with_official.py \
  --data_path "$DATA_PATH" \
  --output_dir "$EVOLVED_RUN_DIR/outputs" \
  --start_idx 200 \
  --end_idx 400
```

## Repository Structure

```text
Trace2Skill/
├── README.md
├── analysis/                           # Error/success trajectory analysis scripts and prompts
│   ├── parse_error_analysis_outputs.py
│   ├── parse_success_analysis_outputs.py
│   ├── run_error_analysis.py           # Agentic error analyst
│   └── run_success_analysis_llm.py     # Single-call success analyst
├── released_skills/                    # Released paper skill artifacts
│   ├── trace2skill-xlsx-35B-combined/
│   ├── trace2skill-xlsx-122B-combined/
│   ├── xlsx-35B/
│   └── xlsx-122B/
├── skill_evolver/                      # Parallel Trace2Skill patch proposal and consolidation
│   ├── run_parallel_skill_evolution.py
│   └── run_parallel_combined_skill_evolution.py
├── spreadsheet_agent/                  # SpreadsheetBench agent and runtime skills
│   ├── agents/
│   ├── skills/
│   └── tools/
├── src/react_agent/                    # ReAct agent and OpenAI-compatible model clients
├── run_spreadsheetbench.py             # SpreadsheetBench runner
├── evaluate_with_official.py           # Official-compatible scorer wrapper
├── analyze_results.py                  # Result/log matching and triage
└── spreadsheetbench_support.py         # Shared SpreadsheetBench utilities
```

Core implementations:

- `skill_evolver/parallel_evolving_agent.py`
- `skill_evolver/parallel_success_evolving_agent.py`
- `skill_evolver/skill_evolving_agent.py`
- `analysis/error_analysis_agent.py`
- `spreadsheet_agent/agents/cli_skill_preloaded_agent.py`

## Acknowledgements

This repository focuses on the spreadsheet setting and released skills discussed in the paper, while keeping the core Trace2Skill evolution pipeline runnable. We thank the developers and communities behind the tools used by this release:

- [Qwen](https://github.com/QwenLM) and the Qwen application ecosystem
- [SpreadsheetBench](https://arxiv.org/abs/2406.14991) for spreadsheet-agent evaluation
- [openpyxl](https://openpyxl.readthedocs.io/) for spreadsheet manipulation support

## License

This project is released under the [Apache License 2.0](https://opensource.org/licenses/Apache-2.0). Released skill artifacts may include their own license files where noted.

## Citation

If you find our work useful in your research, please consider citing our paper:

```bibtex
@misc{ni2026trace2skilldistilltrajectorylocallessons,
      title={Trace2Skill: Distill Trajectory-Local Lessons into Transferable Agent Skills},
      author={Jingwei Ni and Yihao Liu and Xinpeng Liu and Yutao Sun and Mengyu Zhou and Pengyu Cheng and Dexin Wang and Erchao Zhao and Xiaoxi Jiang and Guanjun Jiang},
      year={2026},
      eprint={2603.25158},
      archivePrefix={arXiv},
      primaryClass={cs.AI},
      url={https://arxiv.org/abs/2603.25158},
}
```
