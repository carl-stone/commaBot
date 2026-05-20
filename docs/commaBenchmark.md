# commaBenchmark

Evaluate local LLMs for subagent delegation in the commaBot workflow.

## Why

commaBot runs on Letta Cloud, which has per-token quotas. When the agent launches parallel subagents (e.g., 3 subagents dividing up a milestone's work), token usage scales quickly. Local models on Carl's PC could absorb the cost of well-scoped subagent tasks — but only if they're actually competent at those tasks.

commaBenchmark answers: **which tasks can we safely delegate to a local model, and which require the full model?**

## Task Categories

Based on manual testing with qwen2.5-coder:7b and the kinds of work commaBot actually delegates, we define six categories:

### 1. Edge Case Extraction

**Prompt pattern**: "Read this R function and list all edge cases it handles."

**Why it matters**: When reviewing code, commaBot needs to quickly verify that edge cases are covered. This is retrieval + synthesis — the model reads control flow and identifies guard clauses, early returns, and error paths.

**Manual test result**: qwen2.5-coder:7b correctly identified all 3 edge cases in `.validateModType()`. Strong performance.

**Failure mode**: Missing early returns (e.g., the `if (is.null(mod_type)) return(valid)` path).

### 2. Dependency Identification

**Prompt pattern**: "What external packages does this function call? List each function and its package."

**Why it matters**: When refactoring or adding imports, commaBot needs to know which packages a function depends on. This is pure extraction — no reasoning required, just recognizing function origins.

**Manual test result**: qwen2.5-coder:7b said "no external packages" for a function that calls `mcols()` (GenomicRanges) and `rowRanges()` (SummarizedExperiment). Failed — doesn't recognize Bioconductor accessor functions.

**Failure mode**: Not recognizing Bioconductor package functions (treating `mcols()`, `rowRanges()`, etc. as base R).

### 3. Roxygen2 Documentation

**Prompt pattern**: "Write a roxygen2 documentation block for this S4 method."

**Why it matters**: Every exported function in comma needs roxygen2 docs. This is pattern-matching — the model needs to produce `@param`, `@return`, `@export`, `@examples`, and S4-specific tags (`@docType methods`, `@rdname`).

**Manual test result**: qwen2.5-coder:7b produced a structurally correct roxygen2 block but missed all S4-specific tags (`@docType methods`, `@rdname`). Also used an invalid `mod_type` value (`"mC"` instead of `"6mA"` or `"5mC"`).

**Failure mode**: Produces output that looks professional but violates Bioconductor S4 conventions. Missing `@docType methods` is the most common gap.

### 4. Pattern-Matching Code

**Prompt pattern**: "Here's an example of X. Apply the same pattern to Y."

**Why it matters**: Much of commaBot's delegated work is "apply this pattern to these files" — e.g., add `.validateModType()` calls to 3 functions following the pattern in a 4th. The model needs to recognize the pattern and replicate it accurately.

**Manual test result**: Not yet tested.

**Failure mode**: Replicates the pattern structure but misses domain-specific details (e.g., correct mod_type values, proper S4 dispatch).

### 5. Test Writing

**Prompt pattern**: "Write testthat tests for this function."

**Why it matters**: Every exported function needs tests. The model needs to generate `expect_*()` calls that actually test the function's contract — not just smoke tests that pass trivially.

**Manual test result**: Not yet tested.

**Failure mode**: Generates tests that are syntactically correct but don't test the right things (e.g., `expect_s4_class(obj, "commaData")` is easy; a test that catches the actual bug when `mod_type` is NULL requires understanding the contract).

### 6. Return Value Extraction

**Prompt pattern**: "What does this function return? Include all return paths."

**Why it matters**: Understanding return types and all exit paths is critical for type-safe refactoring. Missing a return path can introduce bugs.

**Manual test result**: qwen2.5-coder:7b said "returns the input mod_type if it passes validation" — missed the `if (is.null(mod_type)) return(valid)` early return, which returns a different type (character vector vs single string).

**Failure mode**: Misses early returns, especially when the return type differs from the main path.

## Scoring System

Three tiers, from most to least automated:

### Tier 1: Execution Testing (fully automated)

For code generation tasks (roxygen2, pattern-matching, test writing). Run the output in the R environment and check:

- **Parse rate**: Does the output parse as valid R? (`parse()` with no error)
- **Execution rate**: Does it run without error?
- **Correctness rate**: Does it produce the expected result?

For test writing specifically: run the generated tests against the real function and count passes.

### Tier 2: Keyword/Pattern Matching (semi-automated)

For extraction and documentation tasks. Check for expected keywords and patterns:

- Does the output contain expected function names? (e.g., `mcols`, `rowRanges`)
- Does it include required roxygen2 tags? (e.g., `@export`, `@docType methods`)
- Does it identify all return paths?

Automated checks flag presence/absence; human confirms significance.

### Tier 3: LLM-as-Judge (automated, stronger model)

For subjective quality assessment. Use Claude or GPT-4 as the judge:

- Judge receives: the prompt, the expected answer, and the model's output
- Judge evaluates: completeness, correctness, convention compliance, code quality
- Judge scores on a 1-5 scale per criterion with brief justification
- This only needs to run once per model comparison, not per-prompt during development

### Quantitative Metrics Per Category

| Category | Primary Metric | Scoring Method |
|---|---|---|
| Edge case extraction | Recall (fraction of edge cases found) | Keyword matching + LLM judge |
| Dependency identification | Precision + Recall (correct packages, no extras) | Keyword matching |
| Roxygen2 documentation | Convention compliance score | Execution (`devtools::document()`) + keyword matching + LLM judge |
| Pattern-matching code | Execution + correctness | Run in R, check output |
| Test writing | Execution + coverage | Run tests against real function, count passes |
| Return value extraction | Recall (all return paths identified) | Keyword matching + LLM judge |

### Composite Score

Weighted average across categories, with execution-based categories weighted higher (they're more objective):

| Category | Weight | Rationale |
|---|---|---|
| Pattern-matching code | 2.0 | Execution-tested, most objective |
| Test writing | 2.0 | Execution-tested, most objective |
| Roxygen2 documentation | 1.5 | Partially execution-tested |
| Edge case extraction | 1.0 | Keyword-matched, semi-objective |
| Dependency identification | 1.0 | Keyword-matched, semi-objective |
| Return value extraction | 1.0 | Keyword-matched, semi-objective |

## Prompt Format

Each prompt is a JSON file containing an array of test cases:

```json
[
  {
    "id": "edge-001",
    "category": "edge-cases",
    "prompt": "Read this R function and list all edge cases it handles:\n\n```r\n.validateModType <- function(mod_type, object) {\n  valid <- levels(mcols(rowRanges(object))$mod_type)\n  if (is.null(mod_type)) {\n    return(valid)\n  }\n  if (length(mod_type) != 1) {\n    stop(\"'mod_type' must be a single string or NULL\")\n  }\n  if (!mod_type %in% valid) {\n    stop(sprintf(\"'mod_type' must be one of: %s\", paste(valid, collapse = \", \")))\n  }\n  mod_type\n}\n```\n\nList each edge case and what the function does for it.",
    "context": "Internal validation helper for the comma R package. Used by all exported functions that accept a mod_type argument.",
    "expected": {
      "edge_cases": [
        "mod_type is NULL → returns all valid mod_types",
        "length(mod_type) != 1 → stops with error",
        "mod_type not in valid → stops with error listing valid values"
      ],
      "keywords": ["NULL", "length", "%in%", "stop", "valid"],
      "scoring_notes": "Must identify all three edge cases. Partial credit for 2/3. Full credit requires describing what the function does for each case, not just listing the condition."
    },
    "max_score": 3
  }
]
```

### Fields

| Field | Required | Description |
|---|---|---|
| `id` | yes | Unique identifier within the category (e.g., `edge-001`) |
| `category` | yes | One of: `edge-cases`, `dependencies`, `roxygen2`, `pattern-matching`, `test-writing`, `return-values` |
| `prompt` | yes | The full prompt text sent to the model |
| `context` | no | Additional context about the function or task |
| `expected` | yes | Object with expected answers, keywords, and scoring notes |
| `max_score` | yes | Maximum score for this prompt |

## Runner Script

`runner.py` — a Python script that:

1. Reads prompt JSON files from `prompts/`
2. Sends each prompt to the Ollama API (`/api/chat`)
3. Collects responses
4. Runs Tier 1 and Tier 2 scoring (execution testing + keyword matching)
5. Formats results for human review (or Tier 3 LLM-as-judge)

### CLI Interface

```bash
# Run all benchmarks against a model
python runner.py --model qwen2.5-coder:7b

# Run a single category
python runner.py --model llama3.1:8b --category edge-cases

# Run with execution testing (requires R environment)
python runner.py --model qwen2.5-coder:7b --exec

# Compare two models
python runner.py --compare llama3.2:3b qwen2.5-coder:7b

# Specify Ollama endpoint
python runner.py --model qwen2.5-coder:7b --host http://carlpc:11434
```

### Output Format

```json
{
  "model": "qwen2.5-coder:7b",
  "timestamp": "2026-05-20T12:00:00Z",
  "categories": {
    "edge-cases": {
      "score": 2.5,
      "max_score": 3,
      "prompts": [
        {
          "id": "edge-001",
          "score": 2.5,
          "max_score": 3,
          "response": "...",
          "keywords_found": ["NULL", "length", "stop"],
          "keywords_missing": [],
          "execution": null,
          "notes": "Identified all 3 edge cases but description of NULL case was imprecise"
        }
      ]
    }
  },
  "composite_score": 0.72,
  "composite_max": 1.0
}
```

## Models to Test

| Model | Size | Notes |
|---|---|---|
| `llama3.2:3b` | 2 GB | Currently on carlpc; baseline |
| `llama3.1:8b` | 4.7 GB | General-purpose 8B; good baseline for the 8B class |
| `qwen2.5-coder:7b` | 4.7 GB | Code-specialized; likely best for our use case |

Future: `deepseek-coder-v2-lite`, `codellama:13b` (if RAM allows), etc.

## File Structure

```
commaBot/
├── benchmark/
│   ├── README.md                    # How to run the benchmarks
│   ├── runner.py                    # Sends prompts, collects & scores responses
│   ├── prompts/
│   │   ├── edge-cases.json          # 3-5 test cases
│   │   ├── dependencies.json        # 3-5 test cases
│   │   ├── roxygen2.json            # 3-5 test cases
│   │   ├── pattern-matching.json    # 3-5 test cases
│   │   ├── test-writing.json        # 3-5 test cases
│   │   └── return-values.json       # 3-5 test cases
│   └── results/                     # Scored results (gitignored)
│       └── .gitkeep
```

### Adding New Prompts

1. Add a test case to the appropriate JSON file in `prompts/`
2. Follow the prompt format specification above
3. Include `expected` with keywords and scoring notes
4. Run `python runner.py --model <model> --category <category>` to test

### Adding New Categories

1. Create a new JSON file in `prompts/`
2. Add the category to the scoring weights table
3. Update `runner.py` to recognize the new category

## Delegation Decision Rule

After benchmarking, we'll define a threshold per category:

- **Score ≥ 0.8**: Safe to delegate. The model handles this task reliably.
- **Score 0.5–0.8**: Delegate with review. The model handles most cases but needs human verification.
- **Score < 0.5**: Don't delegate. The model's error rate is too high; the retry cost exceeds the savings.

This gives commaBot a concrete decision rule: "can I send this subagent task to a local model, or do I need to do it myself?"
