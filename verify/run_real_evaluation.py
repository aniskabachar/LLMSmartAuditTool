"""
verify/run_real_evaluation.py

Full real evaluation pipeline:
  Step 1 — Download SmartBugs contracts (if not already present)
  Step 2 — Create 80/20 train/test splits
  Step 3 — Measure random agent floor on test set
  Step 4 — Run real Groq LLM evaluation on test set
  Step 5 — Save and print final results table

Run:
    py verify/run_real_evaluation.py               # 10 contracts/tier
    py verify/run_real_evaluation.py --n 20        # 20 contracts/tier
    py verify/run_real_evaluation.py --tier easy   # one tier only
"""

from __future__ import annotations
import json, sys, os, argparse
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

from pathlib import Path


def _need_download() -> bool:
    for tier in ["easy", "medium", "hard"]:
        if not list(Path(f"tasks/{tier}").glob("*.sol")):
            return True
    return False


def step1_download():
    print("\n" + "=" * 55)
    print("STEP 1: Fetching SmartBugs contracts from GitHub")
    print("=" * 55)
    if not _need_download():
        totals = {t: len(list(Path(f"tasks/{t}").glob("*.sol")))
                  for t in ["easy","medium","hard"]}
        print(f"  Already downloaded: {totals}")
        return
    import scripts.fetch_smartbugs as fb
    fb.run()


def step2_split():
    print("\n" + "=" * 55)
    print("STEP 2: Creating 80/20 train/test splits")
    print("=" * 55)
    from verify.split_dataset import create_split
    splits = {}
    for level in ["easy", "medium", "hard"]:
        splits[level] = create_split(level, test_ratio=0.2, seed=42)
    return splits


def step3_random_baseline(tiers: list[str]) -> dict:
    print("\n" + "=" * 55)
    print("STEP 3: Random agent baseline (floor check)")
    print("=" * 55)
    print("  Expected: all values < 0.20\n")
    from verify.random_baseline_real import run_random_baseline
    results = {}
    for level in tiers:
        split_file = f"verify/splits/{level}_split.json"
        try:
            r = run_random_baseline(level, n=30, split_file=split_file,
                                    use_test_split=True)
            results[level] = r
            print(f"  {level:<8}: score={r['avg_score']:.4f}  "
                  f"P={r['avg_precision']:.4f}  R={r['avg_recall']:.4f}")
        except FileNotFoundError as e:
            print(f"  {level}: SKIP — {e}")
            results[level] = {"avg_score": 0.0, "avg_precision": 0.0, "avg_recall": 0.0}
    return results


def step4_real_llm(tiers: list[str], n_per_tier: int) -> dict:
    print("\n" + "=" * 55)
    print(f"STEP 4: Real LLM evaluation ({n_per_tier} contracts/tier)")
    print("=" * 55)
    print("  (Uses Groq API — check GROQ_API_KEY in .env)\n")
    from verify.real_evaluator import evaluate_difficulty
    results = {}
    for level in tiers:
        print(f"\n  --- {level.upper()} ---")
        split_file = f"verify/splits/{level}_split.json"
        try:
            r = evaluate_difficulty(
                level, n=n_per_tier,
                split_file=split_file,
                use_test_split=True,
            )
            results[level] = r
        except FileNotFoundError as e:
            print(f"  SKIP — {e}")
            results[level] = {"avg_score": None, "error": str(e)}
        except Exception as e:
            print(f"  ERROR: {e}")
            results[level] = {"avg_score": None, "error": str(e)}
    return results


def step5_report(random_scores: dict, real_scores: dict, tiers: list[str]):
    print("\n\n" + "=" * 65)
    print("FINAL RESULTS — REAL EVALUATION")
    print("=" * 65)
    print(f"\n  {'Tier':<10} {'Random':>9} {'LLM Score':>11} "
          f"{'Precision':>11} {'Recall':>9} {'F1':>7} {'Gap':>8}")
    print("  " + "-" * 63)

    output = {}
    for level in tiers:
        rand = random_scores.get(level, {}).get("avg_score", 0.0)
        llm  = real_scores.get(level, {})
        if llm.get("avg_score") is None:
            print(f"  {level:<10} {rand:>9.4f}  {'ERROR':>11}")
            continue
        score = llm["avg_score"]
        prec  = llm.get("avg_precision", 0.0)
        rec   = llm.get("avg_recall",    0.0)
        f1    = llm.get("avg_f1",        0.0)
        gap   = round(score - rand, 4)
        print(f"  {level:<10} {rand:>9.4f}  {score:>11.4f} "
              f"{prec:>11.4f} {rec:>9.4f} {f1:>7.4f} {gap:>+8.4f}")
        output[level] = {
            "random_score":  rand,
            "llm_score":     score,
            "precision":     prec,
            "recall":        rec,
            "f1":            f1,
            "gap":           gap,
            "n_contracts":   llm.get("n_contracts", 0),
        }

    # Interpret
    print()
    scores = [v["llm_score"] for v in output.values() if "llm_score" in v]
    if scores:
        overall = sum(scores) / len(scores)
        print(f"  Overall mean score: {overall:.4f}")
        if overall > 0.60:
            print("  [STRONG] LLM significantly outperforms random baseline.")
        elif overall > 0.35:
            print("  [MODERATE] LLM outperforms random baseline.")
        else:
            print("  [WEAK] Small gap — model needs improvement.")

    # Save
    result_path = Path("verify/results/real_evaluation.json")
    result_path.parent.mkdir(exist_ok=True, parents=True)
    result_path.write_text(json.dumps({
        "random_baseline":  {k: v.get("avg_score") for k, v in random_scores.items()},
        "real_llm_results": output,
    }, indent=2))
    print(f"\n  Saved => {result_path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--n",    type=int, default=10,
                        help="Contracts per tier for LLM eval (default 10)")
    parser.add_argument("--tier", default="all",
                        choices=["all","easy","medium","hard"],
                        help="Which tier to evaluate (default: all)")
    parser.add_argument("--skip-download", action="store_true")
    args = parser.parse_args()

    tiers = (["easy","medium","hard"] if args.tier == "all"
             else [args.tier])

    if not args.skip_download:
        step1_download()

    step2_split()
    rand  = step3_random_baseline(tiers)
    real  = step4_real_llm(tiers, args.n)
    step5_report(rand, real, tiers)


if __name__ == "__main__":
    main()
