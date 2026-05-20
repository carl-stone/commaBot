#!/usr/bin/env python3
"""
commaBenchmark runner — evaluates local LLMs for subagent delegation.

Sends prompts from JSON files to the Ollama API, collects responses,
and scores them using keyword matching and/or an LLM judge.

Usage:
    python runner.py --model qwen2.5-coder:7b --host http://carlpc:11434
    python runner.py --model llama3.1:8b --category edge-cases
    python runner.py --model qwen2.5-coder:7b --judge --judge-model mlx-community/gemma-4-e2b-it-4bit
    python runner.py --compare llama3.2:3b qwen2.5-coder:7b
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError


# ---------------------------------------------------------------------------
# API clients
# ---------------------------------------------------------------------------

def ollama_chat(model: str, prompt: str, host: str, timeout: int = 120) -> dict:
    """Send a chat prompt to Ollama and return the raw response."""
    url = f"{host.rstrip('/')}/api/chat"
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }).encode()

    req = Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except URLError as e:
        print(f"  ERROR: Failed to connect to Ollama at {url}: {e}", file=sys.stderr)
        sys.exit(1)


def openai_chat(model: str, system: str, user: str, host: str,
                api_key: str = None, timeout: int = 120) -> dict:
    """Send a chat prompt to an OpenAI-compatible API (mlx-lm server, etc.)."""
    url = f"{host.rstrip('/')}/v1/chat/completions"
    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.1,
        "stream": False,
    }).encode()

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    req = Request(url, data=payload, headers=headers)
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except URLError as e:
        print(f"  ERROR: Failed to connect to judge at {url}: {e}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Keyword scoring (Tier 2)
# ---------------------------------------------------------------------------

def score_keywords(response: str, expected: dict) -> dict:
    """Score a response based on keyword presence/absence."""
    found = []
    missing = []
    for kw in expected.get("keywords", []):
        if kw.lower() in response.lower():
            found.append(kw)
        else:
            missing.append(kw)
    return {"found": found, "missing": missing}


def score_edge_cases(response: str, expected: dict) -> dict:
    """Score edge case extraction by checking if each expected case is mentioned."""
    found = []
    missing = []
    for case in expected.get("edge_cases", []):
        key_phrase = case.split("→")[0].strip() if "→" in case else case
        condition_words = re.findall(r'\w+', key_phrase.lower())
        if any(w in response.lower() for w in condition_words if len(w) > 2):
            found.append(case)
        else:
            missing.append(case)
    return {"found": found, "missing": missing}


def score_return_values(response: str, expected: dict) -> dict:
    """Score return value extraction by checking if each return path is mentioned."""
    found = []
    missing = []
    for path in expected.get("return_paths", []):
        key_terms = re.findall(r'\w+', path.lower())
        significant_terms = [t for t in key_terms if len(t) > 3 and t not in
                           ("returns", "return", "with", "from", "that", "this", "when")]
        if any(t in response.lower() for t in significant_terms):
            found.append(path)
        else:
            missing.append(path)
    return {"found": found, "missing": missing}


def score_dependencies(response: str, expected: dict) -> dict:
    """Score dependency identification by checking for expected packages and functions."""
    found_packages = {}
    missing_packages = {}

    for pkg, funcs in expected.get("packages", {}).items():
        pkg_found = pkg.lower() in response.lower()
        funcs_found = [f for f in funcs if f.lower() in response.lower()]
        if pkg_found or len(funcs_found) > 0:
            found_packages[pkg] = funcs_found
        else:
            missing_packages[pkg] = funcs

    return {
        "found_packages": found_packages,
        "missing_packages": missing_packages,
    }


def score_roxygen2(response: str, expected: dict) -> dict:
    """Score roxygen2 documentation by checking for required tags and patterns."""
    found_tags = []
    missing_tags = []
    for tag in expected.get("required_tags", []):
        if tag.lower() in response.lower():
            found_tags.append(tag)
        else:
            missing_tags.append(tag)

    forbidden_found = []
    for pattern in expected.get("forbidden_patterns", []):
        if pattern.lower() in response.lower():
            forbidden_found.append(pattern)

    return {
        "found_tags": found_tags,
        "missing_tags": missing_tags,
        "forbidden_found": forbidden_found,
    }


# Category → keyword scoring function mapping
KEYWORD_SCORERS = {
    "edge-cases": score_edge_cases,
    "dependencies": score_dependencies,
    "roxygen2": score_roxygen2,
    "pattern-matching": score_keywords,
    "test-writing": score_keywords,
    "return-values": score_return_values,
}


def compute_keyword_score(cat: str, score_result: dict, prompt: dict) -> tuple[int, int]:
    """Compute numeric keyword score from scoring result."""
    if cat == "edge-cases":
        n_found = len(score_result.get("found", []))
        return n_found, prompt.get("max_score", n_found)
    elif cat == "dependencies":
        n_found = len(score_result.get("found_packages", {}))
        n_missing = len(score_result.get("missing_packages", {}))
        return n_found, n_found + n_missing
    elif cat == "roxygen2":
        n_found = len(score_result.get("found_tags", []))
        n_missing = len(score_result.get("missing_tags", []))
        n_forbidden = len(score_result.get("forbidden_found", []))
        return max(0, n_found - n_forbidden), n_found + n_missing
    elif cat == "return-values":
        n_found = len(score_result.get("found", []))
        return n_found, prompt.get("max_score", n_found)
    else:
        n_found = len(score_result.get("found", []))
        n_missing = len(score_result.get("missing", []))
        return n_found, n_found + n_missing


# ---------------------------------------------------------------------------
# LLM Judge scoring (Tier 3)
# ---------------------------------------------------------------------------

JUDGE_SYSTEM = """You are an expert evaluator for R/Bioconductor programming tasks. You compare a model's response against the expected answer and score each item for correctness. Be strict: a response that mentions the right concept but gets details wrong is "partial", not "correct". A response that is factually wrong is "incorrect", not "partial"."""


def build_judge_prompt(category: str, expected: dict, model_response: str) -> str:
    """Build the judge prompt for a given category and expected answer."""
    # Strip metadata the judge doesn't need — only send the actual expected items
    judge_expected = {}
    if "edge_cases" in expected:
        judge_expected["edge_cases"] = expected["edge_cases"]
    if "return_paths" in expected:
        judge_expected["return_paths"] = expected["return_paths"]
    if "packages" in expected:
        judge_expected["packages"] = expected["packages"]
    if "required_tags" in expected:
        judge_expected["required_tags"] = expected["required_tags"]
    if "forbidden_patterns" in expected:
        judge_expected["forbidden_patterns"] = expected["forbidden_patterns"]
    if "pattern_elements" in expected:
        judge_expected["pattern_elements"] = expected["pattern_elements"]
    if "test_cases" in expected:
        judge_expected["test_cases"] = expected["test_cases"]
    if not judge_expected:
        judge_expected = expected  # fallback

    expected_str = json.dumps(judge_expected, indent=2, ensure_ascii=False)

    # Truncate model response if very long (judge doesn't need 500 lines of code)
    max_response_chars = 2000
    if len(model_response) > max_response_chars:
        model_response = model_response[:max_response_chars] + "\n... [truncated]"

    # Category-specific evaluation instructions
    category_instructions = {
        "edge-cases": "For each edge case in the expected answer, determine if the model correctly identified it AND correctly described what the function does. An edge case mentioned with the wrong action (e.g., says 'stops' when it should 'returns') is 'partial' at best.",
        "dependencies": "For each package/function in the expected answer, determine if the model correctly identified it. A function attributed to the wrong package is 'incorrect'.",
        "roxygen2": "For each required tag, determine if the model included it. Tags present but with wrong content are 'partial'. Forbidden patterns found should be noted.",
        "pattern-matching": "Evaluate whether the model correctly applied the pattern. Check each expected element: was it included? Was it correct? Was it placed in the right position?",
        "test-writing": "For each expected test case, determine if the model wrote a test that actually tests it. A test that calls the function but doesn't assert the right thing is 'partial'.",
        "return-values": "For each return path in the expected answer, determine if the model correctly identified it. A return path described with the wrong type or wrong value is 'incorrect'.",
    }

    instructions = category_instructions.get(category,
        "For each item in the expected answer, determine if the model's response correctly addresses it.")

    return f"""Evaluate this model response against the expected answer.

Category: {category}

Expected answer:
{expected_str}

Model response:
{model_response}

{instructions}

Rate each item as:
- correct: The model correctly identifies and describes this item
- partial: The model mentions this item but gets some details wrong
- incorrect: The model mentions this item but gets it factually wrong
- missing: The model does not mention this item at all

Output your evaluation as a JSON object with this exact format:
{{
  "items": [
    {{"item": "<brief description>", "rating": "correct|partial|incorrect|missing", "reason": "<one sentence>"}},
    ...
  ],
  "score": <number of correct items>,
  "total": <total number of items>,
  "summary": "<one sentence overall assessment>"
}}

Only output the JSON, nothing else."""


def parse_judge_response(response_text: str) -> dict:
    """Parse the judge's JSON response, handling common formatting issues."""
    # Try to extract JSON from the response (small models may add extra text)
    text = response_text.strip()

    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to find JSON block (between ``` or braces)
    json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(1))
        except json.JSONDecodeError:
            pass

    # Try to find the outermost braces
    brace_match = re.search(r'\{.*\}', text, re.DOTALL)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except json.JSONDecodeError:
            pass

    # Failed to parse — return a fallback
    return {
        "items": [],
        "score": 0,
        "total": 0,
        "summary": f"Failed to parse judge response: {text[:200]}",
        "parse_error": True,
    }


def judge_score(category: str, expected: dict, model_response: str,
                judge_model: str, judge_host: str, judge_api_key: str = None,
                timeout: int = 120) -> dict:
    """Score a response using the LLM judge."""
    judge_prompt = build_judge_prompt(category, expected, model_response)

    resp = openai_chat(judge_model, JUDGE_SYSTEM, judge_prompt, judge_host,
                       api_key=judge_api_key, timeout=timeout)

    # Extract response text from OpenAI-compatible format
    choices = resp.get("choices", [])
    if not choices:
        return {"items": [], "score": 0, "total": 0, "summary": "No response from judge", "parse_error": True}

    content = choices[0].get("message", {}).get("content", "")
    return parse_judge_response(content)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

PROMPTS_DIR = Path(__file__).parent / "prompts"
RESULTS_DIR = Path(__file__).parent / "results"

CATEGORY_WEIGHTS = {
    "pattern-matching": 2.0,
    "test-writing": 2.0,
    "roxygen2": 1.5,
    "edge-cases": 1.0,
    "dependencies": 1.0,
    "return-values": 1.0,
}


def load_prompts(category: str = None) -> list[dict]:
    """Load prompt test cases from JSON files."""
    prompts = []
    if category:
        files = [PROMPTS_DIR / f"{category}.json"]
    else:
        files = sorted(PROMPTS_DIR.glob("*.json"))

    for f in files:
        if f.exists():
            data = json.loads(f.read_text())
            prompts.extend(data)
    return prompts


def run_benchmark(model: str, host: str, category: str = None,
                  exec_test: bool = False, timeout: int = 120,
                  prompt_variant: str = "both",
                  use_judge: bool = False,
                  judge_model: str = None,
                  judge_host: str = "http://localhost:8000",
                  judge_api_key: str = None) -> dict:
    """Run the benchmark for a single model.

    Args:
        prompt_variant: "sparse", "detailed", or "both" (runs each prompt twice)
        use_judge: Whether to use LLM-as-judge scoring
        judge_model: Model name for the judge (e.g., mlx-community/gemma-4-e2b-it-4bit)
        judge_host: Host for the judge API (OpenAI-compatible)
        judge_api_key: API key for the judge server (if required)
    """
    prompts = load_prompts(category)
    if not prompts:
        print(f"No prompts found for category: {category or 'all'}", file=sys.stderr)
        sys.exit(1)

    results = {
        "model": model,
        "host": host,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "judge_enabled": use_judge,
        "judge_model": judge_model,
        "categories": {},
    }

    if prompt_variant == "both":
        variants = ["sparse", "detailed"]
    else:
        variants = [prompt_variant]

    for prompt in prompts:
        pid = prompt["id"]
        cat = prompt["category"]
        difficulty = prompt.get("difficulty", "unknown")

        # Get prompt text(s)
        if "prompts" in prompt:
            prompt_texts = {v: prompt["prompts"][v] for v in variants if v in prompt["prompts"]}
        else:
            prompt_texts = {"legacy": prompt["prompt"]}

        for variant_name, prompt_text in prompt_texts.items():
            label = f"{pid}/{variant_name}" if len(prompt_texts) > 1 else pid
            print(f"  [{label}] ({difficulty}) {prompt_text[:60]}...")

            # Send to candidate model (Ollama)
            t0 = time.time()
            resp = ollama_chat(model, prompt_text, host, timeout=timeout)
            elapsed = time.time() - t0

            content = resp.get("message", {}).get("content", "")
            eval_count = resp.get("eval_count", 0)
            prompt_eval_count = resp.get("prompt_eval_count", 0)
            eval_duration = resp.get("eval_duration", 0)
            tokens_per_sec = eval_count / (eval_duration / 1e9) if eval_duration > 0 else 0

            # Keyword scoring (Tier 2)
            scorer = KEYWORD_SCORERS.get(cat, score_keywords)
            kw_result = scorer(content, prompt.get("expected", {}))
            kw_score, kw_max = compute_keyword_score(cat, kw_result, prompt)

            # Judge scoring (Tier 3) — optional
            judge_result = None
            judge_score_val = None
            judge_max = None
            if use_judge and judge_model:
                print(f"    Judging...", end="", flush=True)
                judge_result = judge_score(cat, prompt.get("expected", {}), content,
                                          judge_model, judge_host,
                                          judge_api_key=judge_api_key,
                                          timeout=timeout)
                judge_score_val = judge_result.get("score", 0)
                judge_max = judge_result.get("total", 0)
                print(f" {judge_score_val}/{judge_max}")

            prompt_result = {
                "id": pid,
                "variant": variant_name,
                "difficulty": difficulty,
                "prompt_tokens": prompt_eval_count,
                "keyword_score": kw_score,
                "keyword_max": kw_max,
                "judge_score": judge_score_val,
                "judge_max": judge_max,
                # Use judge score if available, otherwise keyword score
                "score": judge_score_val if judge_score_val is not None else kw_score,
                "max_score": judge_max if judge_max is not None else kw_max,
                "response": content,
                "elapsed_seconds": round(elapsed, 2),
                "tokens_per_sec": round(tokens_per_sec, 1),
                "keyword_scoring": kw_result,
                "judge_scoring": judge_result,
            }

            # Aggregate by category
            cat_key = cat
            if cat_key not in results["categories"]:
                results["categories"][cat_key] = {
                    "prompts": [], "score": 0, "max_score": 0,
                    "keyword_score": 0, "keyword_max": 0,
                    "judge_score": 0, "judge_max": 0,
                    "by_variant": {}, "by_difficulty": {},
                }
            results["categories"][cat_key]["prompts"].append(prompt_result)
            results["categories"][cat_key]["score"] += prompt_result["score"]
            results["categories"][cat_key]["max_score"] += prompt_result["max_score"]
            results["categories"][cat_key]["keyword_score"] += kw_score
            results["categories"][cat_key]["keyword_max"] += kw_max
            if judge_score_val is not None:
                results["categories"][cat_key]["judge_score"] += judge_score_val
                results["categories"][cat_key]["judge_max"] += judge_max

            # Track by variant
            if variant_name not in results["categories"][cat_key]["by_variant"]:
                results["categories"][cat_key]["by_variant"][variant_name] = {
                    "score": 0, "max_score": 0,
                    "keyword_score": 0, "keyword_max": 0,
                    "judge_score": 0, "judge_max": 0,
                }
            bv = results["categories"][cat_key]["by_variant"][variant_name]
            bv["score"] += prompt_result["score"]
            bv["max_score"] += prompt_result["max_score"]
            bv["keyword_score"] += kw_score
            bv["keyword_max"] += kw_max
            if judge_score_val is not None:
                bv["judge_score"] += judge_score_val
                bv["judge_max"] += judge_max

            # Track by difficulty
            if difficulty not in results["categories"][cat_key]["by_difficulty"]:
                results["categories"][cat_key]["by_difficulty"][difficulty] = {
                    "score": 0, "max_score": 0,
                    "keyword_score": 0, "keyword_max": 0,
                    "judge_score": 0, "judge_max": 0,
                }
            bd = results["categories"][cat_key]["by_difficulty"][difficulty]
            bd["score"] += prompt_result["score"]
            bd["max_score"] += prompt_result["max_score"]
            bd["keyword_score"] += kw_score
            bd["keyword_max"] += kw_max
            if judge_score_val is not None:
                bd["judge_score"] += judge_score_val
                bd["judge_max"] += judge_max

            # Print inline result
            score_str = f"kw={kw_score}/{kw_max}"
            if judge_score_val is not None:
                score_str += f" judge={judge_score_val}/{judge_max}"
            print(f"    {score_str} ({elapsed:.1f}s, {tokens_per_sec:.0f} tok/s, {prompt_eval_count} prompt tok)")

    # Calculate composite score
    total_weighted = 0
    total_weight = 0
    for cat, data in results["categories"].items():
        weight = CATEGORY_WEIGHTS.get(cat, 1.0)
        ratio = data["score"] / data["max_score"] if data["max_score"] > 0 else 0
        total_weighted += ratio * weight
        total_weight += weight

    results["composite_score"] = round(total_weighted / total_weight, 3) if total_weight > 0 else 0
    return results


def compare_models(models: list[str], host: str, category: str = None,
                   timeout: int = 120, prompt_variant: str = "both",
                   use_judge: bool = False, judge_model: str = None,
                   judge_host: str = "http://localhost:8000",
                   judge_api_key: str = None) -> None:
    """Run benchmarks for multiple models and compare."""
    all_results = {}
    for model in models:
        print(f"\n{'='*60}")
        print(f"Model: {model}")
        print(f"{'='*60}")
        results = run_benchmark(model, host, category, timeout=timeout,
                               prompt_variant=prompt_variant,
                               use_judge=use_judge, judge_model=judge_model,
                               judge_host=judge_host, judge_api_key=judge_api_key)
        all_results[model] = results

    # Print comparison table
    print(f"\n{'='*60}")
    print("COMPARISON")
    print(f"{'='*60}")
    print(f"{'Category':<20}", end="")
    for model in models:
        print(f"{model:>20}", end="")
    print()
    print("-" * (20 + 20 * len(models)))

    all_categories = set()
    for model, results in all_results.items():
        all_categories.update(results["categories"].keys())

    for cat in sorted(all_categories):
        print(f"{cat:<20}", end="")
        for model in models:
            data = all_results[model]["categories"].get(cat, {})
            score = data.get("score", 0)
            max_score = data.get("max_score", 0)
            ratio = score / max_score if max_score > 0 else 0
            print(f"{score}/{max_score} ({ratio:.0%}){'':>5}", end="")
        print()

    print(f"{'COMPOSITE':<20}", end="")
    for model in models:
        print(f"{all_results[model]['composite_score']:>17.1%}   ", end="")
    print()


def save_results(results: dict, model: str) -> Path:
    """Save results to a JSON file."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = RESULTS_DIR / f"{model.replace(':', '_')}-{ts}.json"
    filename.write_text(json.dumps(results, indent=2))
    return filename


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="commaBenchmark runner")
    parser.add_argument("--model", help="Model name (e.g., qwen2.5-coder:7b)")
    parser.add_argument("--compare", nargs="+", help="Compare multiple models")
    parser.add_argument("--category", help="Run a single category")
    parser.add_argument("--host", default="http://localhost:11434",
                        help="Ollama API endpoint (default: http://localhost:11434)")
    parser.add_argument("--exec", action="store_true",
                        help="Enable execution testing (requires R environment)")
    parser.add_argument("--timeout", type=int, default=120,
                        help="Timeout per request in seconds (default: 120)")
    parser.add_argument("--no-save", action="store_true",
                        help="Don't save results to file")
    parser.add_argument("--variant", choices=["sparse", "detailed", "both"],
                        default="both",
                        help="Prompt variant: sparse, detailed, or both (default: both)")
    parser.add_argument("--judge", action="store_true",
                        help="Enable LLM-as-judge scoring")
    parser.add_argument("--judge-model", default="mlx-community/gemma-4-e2b-it-4bit",
                        help="Judge model name (default: mlx-community/gemma-4-e2b-it-4bit)")
    parser.add_argument("--judge-host", default="http://localhost:8000",
                        help="Judge API endpoint (default: http://localhost:8000)")
    parser.add_argument("--judge-api-key", default=os.environ.get("MLX_API_KEY"),
                        help="Judge API key (or set MLX_API_KEY env var)")
    args = parser.parse_args()

    if args.compare:
        compare_models(args.compare, args.host, args.category, args.timeout,
                      args.variant, args.judge, args.judge_model,
                      args.judge_host, args.judge_api_key)
    elif args.model:
        judge_str = f", judge={args.judge_model}" if args.judge else ""
        print(f"Running benchmark: model={args.model}, host={args.host}, variant={args.variant}{judge_str}")
        results = run_benchmark(args.model, args.host, args.category,
                               exec_test=args.exec, timeout=args.timeout,
                               prompt_variant=args.variant,
                               use_judge=args.judge,
                               judge_model=args.judge_model if args.judge else None,
                               judge_host=args.judge_host,
                               judge_api_key=args.judge_api_key)

        # Print summary
        print(f"\n{'='*60}")
        print("SUMMARY")
        print(f"{'='*60}")
        for cat, data in results["categories"].items():
            kw_ratio = data["keyword_score"] / data["keyword_max"] if data["keyword_max"] > 0 else 0
            line = f"  {cat:<20} kw={data['keyword_score']}/{data['keyword_max']} ({kw_ratio:.0%})"
            if args.judge and data["judge_max"] > 0:
                j_ratio = data["judge_score"] / data["judge_max"]
                line += f"  judge={data['judge_score']}/{data['judge_max']} ({j_ratio:.0%})"
            print(line)
        print(f"  {'COMPOSITE':<20} {results['composite_score']:.1%}")

        if not args.no_save:
            path = save_results(results, args.model)
            print(f"\nResults saved to: {path}")
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
