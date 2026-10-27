"""
verify/regrade.py

Recomputes every number in a results file written by
verify/run_real_evaluation.py, without calling the LLM:

  - re-grades each contract's stored findings against tasks/*/<contract>.json
  - recomputes per-tier and overall summaries
  - re-runs the seeded random baseline
  - checks every graded contract belongs to the held-out test split

Exits 0 if everything matches the file, 1 otherwise.
Use --write to rewrite the file with the recomputed numbers (e.g. after a
ground-truth or grader fix) instead of failing.

Run:
    py verify/regrade.py verify/results/real_evaluation.json
"""

from __future__ import annotations
import json, sys, argparse
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

from pathlib import Path
from auditor.models import Action
from verify.eval_common import ROOT, contract_record, summarize, split_path, rel
from verify.random_baseline_real import run_random_baseline
from verify.run_real_evaluation import build_summary


def _diff(path: str, old, new, out: list[str]):
    """Collect human-readable differences between two JSON-like values."""
    if isinstance(old, dict) and isinstance(new, dict):
        for k in sorted(set(old) | set(new)):
            _diff(f"{path}.{k}", old.get(k), new.get(k), out)
    elif isinstance(old, list) and isinstance(new, list) and len(old) == len(new):
        for i, (o, n) in enumerate(zip(old, new)):
            _diff(f"{path}[{i}]", o, n, out)
    elif old != new:
        out.append(f"{path}: file={old!r} recomputed={new!r}")


def regrade(data: dict) -> tuple[dict, list[str], list[str]]:
    """Return (recomputed results document, number differences, test-split leaks)."""
    problems: list[str] = []
    leaks:    list[str] = []
    meta    = data.get("metadata", {})
    results = data.get("results")
    if not results or not all(r.get("details") and all("action" in d for d in r["details"])
                              for r in results.values()):
        raise ValueError("File has no stored per-contract findings "
                         "(written before the reproducible format); it cannot be re-graded.")

    tiers = list(results)
    new_results = {}
    for tier in tiers:
        test_stems = set(json.loads(split_path(tier).read_text(encoding="utf-8"))["test"])
        details = []
        for d in results[tier]["details"]:
            sol_path = ROOT / "tasks" / tier / d["contract"]
            if sol_path.stem not in test_stems:
                leaks.append(f"{tier}/{d['contract']}: not in held-out test split")
            details.append(contract_record(sol_path, Action(**d["action"]),
                                           d.get("raw_responses", []), d.get("error")))
        new_results[tier] = summarize(tier, details)

    new_random = {
        tier: run_random_baseline(tier, n=meta.get("n"), seed=meta["random_seed"],
                                  use_test_split=True, trials=meta["random_trials"])
        for tier in tiers
    }
    rows, overall = build_summary(new_random, new_results, tiers)

    new_data = {**data, "random_baseline": new_random, "summary": rows,
                "overall": overall, "results": new_results}
    for key in ("results", "random_baseline", "summary", "overall"):
        _diff(key, data.get(key), new_data[key], problems)
    return new_data, problems, leaks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("results_file", nargs="?",
                        default="verify/results/real_evaluation.json")
    parser.add_argument("--write", action="store_true",
                        help="Rewrite the file with recomputed numbers")
    args = parser.parse_args()

    path = Path(args.results_file)
    if not path.is_absolute():
        path = ROOT / path
    data = json.loads(path.read_text(encoding="utf-8"))

    try:
        new_data, problems, leaks = regrade(data)
    except ValueError as e:
        print(f"[FAIL] {path.name}: {e}")
        sys.exit(1)

    meta = data.get("metadata", {})
    print(f"Results file : {rel(path)}")
    print(f"Evaluator    : {meta.get('evaluator')}  model={meta.get('model')}  "
          f"prompt={meta.get('prompt_hash')}  commit={meta.get('git_commit')}")
    for tier, r in new_data["summary"].items():
        print(f"  {tier:<8} n={r['n_contracts']:<3} score={r['llm_score']:.4f}  "
              f"P={r['precision']:.4f}  R={r['recall']:.4f}  F1={r['f1']:.4f}  "
              f"random={r['random_score']:.4f}")

    if leaks:
        print(f"[FAIL] {len(leaks)} graded contract(s) are not in the test split:")
        for p in leaks:
            print(f"  - {p}")
        sys.exit(1)
    if not problems:
        print("[PASS] All numbers reproduce from stored findings + ground truth.")
        return
    print(f"[{'UPDATED' if args.write else 'FAIL'}] {len(problems)} difference(s):")
    for p in problems[:50]:
        print(f"  - {p}")
    if args.write:
        path.write_text(json.dumps(new_data, indent=2), encoding="utf-8")
        print(f"  Rewrote {rel(path)}")
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
