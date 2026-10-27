"""
verify/random_baseline_real.py

Runs a completely random agent on real SmartBugs contracts.
Proves the discriminative floor is genuinely low (<0.20),
unlike the simulation which had an 82%+ floor.

A random agent guesses vulnerability types from the valid set with
no information from the contract code.
"""

from __future__ import annotations
import json, random, sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

from auditor.models import (
    Action, DetectedVulnerability, VulnerabilityType, Severity
)
from auditor.grader import grade
from verify.eval_common import load_contracts, select_contracts

VULN_TYPES = list(VulnerabilityType)
SEVERITIES = list(Severity)


def random_action(rng: random.Random, max_bugs: int = 3) -> Action:
    """
    Generates a completely random audit report.
    Number of reported bugs: 0 to max_bugs (uniform).
    Types and severities: uniform random.
    """
    n = rng.randint(0, max_bugs)
    vulns = [
        DetectedVulnerability(
            type        = rng.choice(VULN_TYPES),
            location    = "unknown",
            severity    = rng.choice(SEVERITIES),
            explanation = "random agent",
        )
        for _ in range(n)
    ]
    return Action(vulnerabilities=vulns)


def run_random_baseline(
    difficulty: str,
    n: int | None = None,
    seed: int = 7777,
    use_test_split: bool = True,
    trials: int = 100,
    labels: str = "curated",
) -> dict:
    """
    Run random agent on real SmartBugs contracts.

    Parameters
    ----------
    n              : number of contracts (None = the whole test split)
    seed           : seeds both contract sub-sampling and the random agent
    use_test_split : if True, only held-out test contracts are scored
    trials         : random reports drawn per contract; scores are averaged
                     so the floor estimate is stable on small test splits
    labels         : "curated" (category labels) or "all" (+ tool findings)

    Returns
    -------
    dict with avg_score, avg_precision, avg_recall
    """
    sample = select_contracts(load_contracts(difficulty, use_test_split), n, seed)
    rng    = random.Random(seed)
    scores, precisions, recalls = [], [], []

    for sol_path in sample:
        ground_truth = json.loads(sol_path.with_suffix(".json").read_text(encoding="utf-8"))
        for _ in range(trials):
            result = grade(random_action(rng, max_bugs=3), ground_truth, labels=labels)
            scores.append(result["score"])
            precisions.append(result["precision"])
            recalls.append(result["recall"])

    mean = lambda xs: round(sum(xs) / len(xs), 4) if xs else 0.0
    return {
        "difficulty":    difficulty,
        "n_evaluated":   len(sample),
        "trials":        trials,
        "seed":          seed,
        "test_split":    use_test_split,
        "labels":        labels,
        "avg_score":     mean(scores),
        "avg_precision": mean(precisions),
        "avg_recall":    mean(recalls),
    }


if __name__ == "__main__":
    print("=" * 50)
    print("RANDOM BASELINE ON REAL SMARTBUGS CONTRACTS")
    print("=" * 50)
    print("(Random agent has no access to contract code; test split only)\n")

    all_results = {}
    for level in ["easy", "medium", "hard"]:
        try:
            r = run_random_baseline(level)
            all_results[level] = r
            print(f"  {level:<8}: score={r['avg_score']:.4f}  "
                  f"P={r['avg_precision']:.4f}  R={r['avg_recall']:.4f}  "
                  f"(n={r['n_evaluated']} x {r['trials']} trials)")
        except FileNotFoundError as e:
            print(f"  {level}: SKIP — {e}")

    print()
    if all_results:
        avg = sum(r["avg_score"] for r in all_results.values()) / len(all_results)
        if avg < 0.20:
            print(f"  [PASS] Avg random score = {avg:.4f} < 0.20 — floor is real")
        else:
            print(f"  [FAIL] Avg random score = {avg:.4f} >= 0.20 — floor too high")
