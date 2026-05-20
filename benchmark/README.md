# commaBenchmark

Evaluate local LLMs for subagent delegation in the commaBot workflow.

## Quick Start

```bash
# Run all benchmarks against a model
python runner.py --model qwen2.5-coder:7b --host http://carlpc:11434

# Run with LLM judge (Gemma 4 E2B on MLX, laptop)
python runner.py --model qwen2.5-coder:7b --host http://carlpc:11434 \
    --judge --judge-model mlx-community/gemma-4-e2b-it-4bit --judge-host http://localhost:8000

# Run a single category
python runner.py --model llama3.1:8b --category edge-cases --host http://carlpc:11434

# Compare two models
python runner.py --compare llama3.2:3b qwen2.5-coder:7b --host http://carlpc:11434
```

## Architecture

```
Candidate model (Ollama on carlpc)  →  responses
                                          ↓
Judge model (MLX on laptop)         ←  responses + expected answers
                                          ↓
                                     keyword score + judge score
```

The candidate model runs on carlpc via Ollama. The judge model runs on your laptop via MLX (Apple Silicon). Two separate inference endpoints, one benchmark.

## Scoring

- **Keyword matching** (default): Fast, approximate — checks if expected keywords appear in the response
- **LLM judge** (`--judge`): Slower, more accurate — a small model (Gemma 4 E2B) compares the response against the expected answer and rates each item as correct/partial/incorrect/missing

When the judge is enabled, its score replaces the keyword score as the primary metric. Both scores are always saved in the results JSON.

## Adding New Prompts

1. Add a test case to the appropriate JSON file in `prompts/`
2. Follow the format in [docs/commaBenchmark.md](../docs/commaBenchmark.md)
3. Include `expected` with keywords and scoring notes
4. Re-run the benchmark

## Adding New Categories

1. Create a new JSON file in `prompts/`
2. Add a scoring function in `runner.py` (SCORERS dict)
3. Add the category weight to CATEGORY_WEIGHTS
4. Update `docs/commaBenchmark.md`

## Results

Results are saved to `results/` as JSON files (gitignored). Each file contains per-prompt scores, category totals, and a composite score.

## Full Spec

See [docs/commaBenchmark.md](../docs/commaBenchmark.md) for the complete specification.
