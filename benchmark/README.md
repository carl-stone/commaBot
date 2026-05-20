# commaBenchmark

Evaluate local LLMs for subagent delegation in the commaBot workflow.

## Quick Start

```bash
# Run all benchmarks against a model
python runner.py --model qwen2.5-coder:7b --host http://carlpc:11434

# Run a single category
python runner.py --model llama3.1:8b --category edge-cases --host http://carlpc:11434

# Compare two models
python runner.py --compare llama3.2:3b qwen2.5-coder:7b --host http://carlpc:11434
```

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
