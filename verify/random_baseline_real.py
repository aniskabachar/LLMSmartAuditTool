"""
verify/random_baseline_real.py

Runs a completely random agent on real SmartBugs contracts.
Proves the discriminative floor is genuinely low (<0.20),
unlike the simulation which had an 82%+ floor.

A random agent guesses vulnerability types from the valid set with
no information from the contract code.
"""

from __future__ import annotations
import json, random, sys, os
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

from pathlib import Path
from auditor.models import (
    Action, DetectedVulnerability, VulnerabilityType, Severity
)
from auditor.grader import grade

VULN_TYPES = list(VulnerabilityType)
SEVERITIES = list(Severity)


def random_action(max_bugs: int = 3) -> Action:
    """
    Generates a completely random audit report.
    Number of reported bugs: 0 to max_bugs (uniform).
    Types and severities: uniform random.
    """
    n = random.randint(0, max_bugs)
    vulns = [
        DetectedVulnerability(
            type        = random.choice(VULN_TYPES),
            location    = "unknown",
            severity    = random.choice(SEVERITIES),
            explanation = "random agent",
        )
        for _ in range(n)
    ]
    return Action(vulnerabilities=vulns)


def run_random_baseline(
    difficulty: str,
    n: int = 30,
    seed: int = 7777,
    split_file: str | None = None,
    use_test_split: bool = False,
) -> dict:
    """
    Run random agent on n real SmartBugs contracts.

    Returns
    -------
    dict with avg_score, avg_precision, avg_recall and per-contract details
    """
    random.seed(seed)
    task_dir  = Path(f"tasks/{difficulty}")
    sol_files = sorted(task_dir.glob("*.sol"))

    if not sol_files:
        raise FileNotFoundError(
            f"No .sol files in tasks/{difficulty}/. "
            f"Run: py scripts/fetch_smartbugs.py"
        )

    # Optional: restrict to test split
    if split_file and use_test_split:
        try:
            split_data = json.loads(Path(split_file).read_text())
            test_stems = set(split_data.get("test", []))
            sol_files  = [f for f in sol_files if f.stem in test_stems]
        except Exception as e:
            print(f"  [WARN] split load failed: {e}")

    sample  = random.sample(sol_files, min(n, len(sol_files)))
    scores, precisions, recalls = [], [], []

    for sol_path in sample:
        gt_path = sol_path.with_suffix(".json")
        if not gt_path.exists():
            continue
        ground_truth = json.loads(gt_path.read_text(encoding="utf-8"))
        action       = random_action(max_bugs=3)
        result       = grade(action, ground_truth)
        scores.append(result["score"])
        precisions.append(result["precision"])
        recalls.append(result["recall"])

    avg_score = round(sum(scores) / len(scores), 4) if scores else 0.0
    avg_prec  = round(sum(precisions) / len(precisions), 4) if precisions else 0.0
    avg_rec   = round(sum(recalls)    / len(recalls),    4) if recalls    else 0.0

    return {
        "difficulty":    difficulty,
        "n_evaluated":   len(scores),
        "avg_score":     avg_score,
        "avg_precision": avg_prec,
        "avg_recall":    avg_rec,
    }


if __name__ == "__main__":
    print("=" * 50)
    print("RANDOM BASELINE ON REAL SMARTBUGS CONTRACTS")
    print("=" * 50)
    print("(Random agent has no access to contract code)\n")

    all_results = {}
    for level in ["easy", "medium", "hard"]:
        try:
            r = run_random_baseline(level, n=30)
            all_results[level] = r
            print(f"  {level:<8}: score={r['avg_score']:.4f}  "
                  f"P={r['avg_precision']:.4f}  R={r['avg_recall']:.4f}")
        except FileNotFoundError as e:
            print(f"  {level}: SKIP — {e}")

    print()
    if all_results:
        avg = sum(r["avg_score"] for r in all_results.values()) / len(all_results)
        if avg < 0.20:
            print(f"  [PASS] Avg random score = {avg:.4f} < 0.20 — floor is real")
        else:
            print(f"  [FAIL] Avg random score = {avg:.4f} >= 0.20 — floor too high")
