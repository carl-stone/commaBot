#!/usr/bin/env python3
"""
commaBenchmark runner — evaluates local LLMs for subagent delegation.

Sends prompts from JSON files to the Ollama API, collects responses,
and scores them using keyword matching and (optionally) execution testing.

Usage:
    python runner.py --model qwen2.5-coder:7b
    python runner.py --model llama3.1:8b --category edge-cases
    python runner.py --model qwen2.5-coder:7b --exec
    python runner.py --compare llama3.2:3b qwen2.5-coder:7b
    python runner.py --model qwen2.5-coder:7b --host http://carlpc:11434
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
# Ollama API
# ---------------------------------------------------------------------------

def chat(model: str, prompt: str, host: str, timeout: int = 120) -> dict:
    """Send a chat prompt to Ollama and return the response."""
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


# ---------------------------------------------------------------------------
# Scoring
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
        # Check if key parts of the edge case are mentioned
        # Use the first distinctive word/phrase as a proxy
        key_phrase = case.split("→")[0].strip() if "→" in case else case
        # Extract the condition part (before the arrow)
        condition_words = re.findall(r'\w+', key_phrase.lower())
        # Check if most condition words appear in the response
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
        # Extract key terms from the expected return path
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
    false_positives = []

    for pkg, funcs in expected.get("packages", {}).items():
        pkg_found = pkg.lower() in response.lower()
        funcs_found = [f for f in funcs if f.lower() in response.lower()]
        if pkg_found or len(funcs_found) > 0:
            found_packages[pkg] = funcs_found
        else:
            missing_packages[pkg] = funcs

    # Check for false positives (listing base R as external)
    for pkg, funcs in expected.get("not_packages", {}).items():
        for f in funcs:
            if f.lower() in response.lower() and pkg.lower() not in response.lower():
                # Function mentioned but not attributed to base R
                pass  # We don't penalize this — it's ambiguous

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


# Category → scoring function mapping
SCORERS = {
    "edge-cases": score_edge_cases,
    "dependencies": score_dependencies,
    "roxygen2": score_roxygen2,
    "pattern-matching": score_keywords,
    "test-writing": score_keywords,
    "return-values": score_return_values,
}


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

PROMPTS_DIR = Path(__file__).parent / "prompts"
RESULTS_DIR = Path(__file__).parent / "results"

# Category weights for composite score
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
                  prompt_variant: str = "both") -> dict:
    """Run the benchmark for a single model.

    Args:
        prompt_variant: "sparse", "detailed", or "both" (runs each prompt twice)
    """
    prompts = load_prompts(category)
    if not prompts:
        print(f"No prompts found for category: {category or 'all'}", file=sys.stderr)
        sys.exit(1)

    results = {
        "model": model,
        "host": host,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "categories": {},
    }

    # Determine which variants to run
    if prompt_variant == "both":
        variants = ["sparse", "detailed"]
    else:
        variants = [prompt_variant]

    for prompt in prompts:
        pid = prompt["id"]
        cat = prompt["category"]
        difficulty = prompt.get("difficulty", "unknown")

        # Get prompt text(s) — support both old format (single "prompt") and new format ("prompts.sparse"/"prompts.detailed")
        if "prompts" in prompt:
            prompt_texts = {v: prompt["prompts"][v] for v in variants if v in prompt["prompts"]}
        else:
            # Legacy format: single "prompt" field
            prompt_texts = {"legacy": prompt["prompt"]}

        for variant_name, prompt_text in prompt_texts.items():
            label = f"{pid}/{variant_name}" if len(prompt_texts) > 1 else pid
            print(f"  [{label}] ({difficulty}) {prompt_text[:60]}...")

            # Send to Ollama
            t0 = time.time()
            resp = chat(model, prompt_text, host, timeout=timeout)
            elapsed = time.time() - t0

            content = resp.get("message", {}).get("content", "")
            eval_count = resp.get("eval_count", 0)
            eval_duration = resp.get("eval_duration", 0)
            tokens_per_sec = eval_count / (eval_duration / 1e9) if eval_duration > 0 else 0

            # Score
            scorer = SCORERS.get(cat, score_keywords)
            score_result = scorer(content, prompt.get("expected", {}))

            # Calculate numeric score
            if cat == "edge-cases":
                n_found = len(score_result.get("found", []))
                max_score = prompt.get("max_score", n_found)
                numeric_score = n_found
            elif cat == "dependencies":
                n_found = len(score_result.get("found_packages", {}))
                n_missing = len(score_result.get("missing_packages", {}))
                numeric_score = n_found
                max_score = n_found + n_missing
            elif cat == "roxygen2":
                n_found = len(score_result.get("found_tags", []))
                n_missing = len(score_result.get("missing_tags", []))
                n_forbidden = len(score_result.get("forbidden_found", []))
                numeric_score = max(0, n_found - n_forbidden)
                max_score = n_found + n_missing
            elif cat == "return-values":
                n_found = len(score_result.get("found", []))
                max_score = prompt.get("max_score", n_found)
                numeric_score = n_found
            else:
                # Generic keyword scoring
                n_found = len(score_result.get("found", []))
                n_missing = len(score_result.get("missing", []))
                numeric_score = n_found
                max_score = n_found + n_missing

            prompt_result = {
                "id": pid,
                "variant": variant_name,
                "difficulty": difficulty,
                "score": numeric_score,
                "max_score": max_score,
                "response": content,
                "elapsed_seconds": round(elapsed, 2),
                "tokens_per_sec": round(tokens_per_sec, 1),
                "scoring": score_result,
            }

            # Key results by category/variant for comparison
            cat_key = cat
            if cat_key not in results["categories"]:
                results["categories"][cat_key] = {"prompts": [], "score": 0, "max_score": 0, "by_variant": {}, "by_difficulty": {}}
            results["categories"][cat_key]["prompts"].append(prompt_result)
            results["categories"][cat_key]["score"] += numeric_score
            results["categories"][cat_key]["max_score"] += max_score

            # Track by variant
            if variant_name not in results["categories"][cat_key]["by_variant"]:
                results["categories"][cat_key]["by_variant"][variant_name] = {"score": 0, "max_score": 0}
            results["categories"][cat_key]["by_variant"][variant_name]["score"] += numeric_score
            results["categories"][cat_key]["by_variant"][variant_name]["max_score"] += max_score

            # Track by difficulty
            if difficulty not in results["categories"][cat_key]["by_difficulty"]:
                results["categories"][cat_key]["by_difficulty"][difficulty] = {"score": 0, "max_score": 0}
            results["categories"][cat_key]["by_difficulty"][difficulty]["score"] += numeric_score
            results["categories"][cat_key]["by_difficulty"][difficulty]["max_score"] += max_score

            print(f"    Score: {numeric_score}/{max_score} ({elapsed:.1f}s, {tokens_per_sec:.0f} tok/s)")

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
                   timeout: int = 120, prompt_variant: str = "both") -> None:
    """Run benchmarks for multiple models and compare."""
    all_results = {}
    for model in models:
        print(f"\n{'='*60}")
        print(f"Model: {model}")
        print(f"{'='*60}")
        results = run_benchmark(model, host, category, timeout=timeout,
                               prompt_variant=prompt_variant)
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
    args = parser.parse_args()

    if args.compare:
        compare_models(args.compare, args.host, args.category, args.timeout, args.variant)
    elif args.model:
        print(f"Running benchmark: model={args.model}, host={args.host}, variant={args.variant}")
        results = run_benchmark(args.model, args.host, args.category,
                               exec_test=args.exec, timeout=args.timeout,
                               prompt_variant=args.variant)

        # Print summary
        print(f"\n{'='*60}")
        print("SUMMARY")
        print(f"{'='*60}")
        for cat, data in results["categories"].items():
            ratio = data["score"] / data["max_score"] if data["max_score"] > 0 else 0
            print(f"  {cat:<20} {data['score']}/{data['max_score']} ({ratio:.0%})")
        print(f"  {'COMPOSITE':<20} {results['composite_score']:.1%}")

        if not args.no_save:
            path = save_results(results, args.model)
            print(f"\nResults saved to: {path}")
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
