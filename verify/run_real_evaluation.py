"""
verify/run_real_evaluation.py

Full real evaluation pipeline:
  Step 1 — Download SmartBugs contracts (if not already present)
  Step 2 — Load 80/20 train/test splits (created once, seed=42, if missing)
  Step 3 — Measure random agent floor on test set
  Step 4 — Run real Groq LLM evaluation on test set
  Step 5 — Save and print final results table

The results file records the evaluator config (model, prompt hash, seeds),
git commit, and every contract's parsed findings and raw LLM output.
`py verify/regrade.py <results file>` recomputes every number in it from
the stored findings and the ground truth, without calling the LLM.

Run:
    py verify/run_real_evaluation.py                   # whole test split, basic evaluator
    py verify/run_real_evaluation.py --evaluator cot   # chain-of-thought evaluator
    py verify/run_real_evaluation.py --evaluator ba    # real audit modes: ba|ta|hybrid|rl
    py verify/compare_results.py                       # table across all saved runs
    py verify/run_real_evaluation.py --tier easy       # one tier only
    py verify/run_real_evaluation.py --n 5             # seeded 5-contract subsample/tier
"""

from __future__ import annotations
import json, sys, argparse
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

from pathlib import Path
from auditor.grader import LABEL_SETS
from verify.eval_common import ROOT, TIERS, SAMPLE_SEED, split_path, rel, run_metadata

RANDOM_SEED   = 7777
RANDOM_TRIALS = 100
EVALUATORS    = ("basic", "cot", "ba", "ta", "hybrid", "rl")
DEFAULT_OUT   = {
    "basic": "verify/results/real_evaluation.json",
    "cot":   "verify/results/real_evaluation_cot.json",
    **{m: f"verify/results/real_evaluation_{m}.json" for m in ("ba", "ta", "hybrid", "rl")},
}
COST_KEYS     = ("avg_tokens", "avg_cost_usd", "avg_rounds", "avg_calls", "mode_counts")


def _need_download() -> bool:
    for tier in TIERS:
        if not list((ROOT / "tasks" / tier).glob("*.sol")):
            return True
    return False


def step1_download():
    print("\n" + "=" * 55)
    print("STEP 1: Fetching SmartBugs contracts from GitHub")
    print("=" * 55)
    if not _need_download():
        totals = {t: len(list((ROOT / "tasks" / t).glob("*.sol"))) for t in TIERS}
        print(f"  Already downloaded: {totals}")
        return
    import scripts.fetch_smartbugs as fb
    fb.run()


def step2_split(tiers: list[str]) -> dict:
    print("\n" + "=" * 55)
    print("STEP 2: Loading 80/20 train/test splits")
    print("=" * 55)
    from verify.split_dataset import create_split
    splits = {}
    for level in tiers:
        sp = split_path(level)
        if sp.exists():
            splits[level] = json.loads(sp.read_text(encoding="utf-8"))
            print(f"  {level:<8}: existing split  train={splits[level]['train_size']}  "
                  f"test={splits[level]['test_size']}")
        else:
            splits[level] = create_split(level, test_ratio=0.2, seed=42)
    return splits


def step3_random_baseline(tiers: list[str], n: int | None,
                          seed: int, trials: int, labels: str) -> dict:
    print("\n" + "=" * 55)
    print("STEP 3: Random agent baseline (floor check, test split)")
    print("=" * 55)
    print("  Expected: all values < 0.20\n")
    from verify.random_baseline_real import run_random_baseline
    results = {}
    for level in tiers:
        r = run_random_baseline(level, n=n, seed=seed, use_test_split=True,
                                trials=trials, labels=labels)
        results[level] = r
        print(f"  {level:<8}: score={r['avg_score']:.4f}  "
              f"P={r['avg_precision']:.4f}  R={r['avg_recall']:.4f}")
    return results


def step4_real_llm(evaluate, tiers: list[str], n: int | None, seed: int,
                   labels: str) -> dict:
    print("\n" + "=" * 55)
    print("STEP 4: Real LLM evaluation (test split)")
    print("=" * 55)
    print("  (Uses Groq API — check GROQ_API_KEY in .env)\n")
    results = {}
    for level in tiers:
        print(f"\n  --- {level.upper()} ---")
        results[level] = evaluate(level, n=n, seed=seed, use_test_split=True, labels=labels)
    return results


def build_summary(random_scores: dict, real_scores: dict, tiers: list[str]) -> tuple[dict, dict]:
    """Per-tier comparison rows and overall means, derived only from the inputs."""
    rows = {}
    for level in tiers:
        llm  = real_scores[level]
        rand = random_scores[level]["avg_score"]
        rows[level] = {
            "random_score": rand,
            "llm_score":    llm["avg_score"],
            "precision":    llm["avg_precision"],
            "recall":       llm["avg_recall"],
            "f1":           llm["avg_f1"],
            "gap":          round(llm["avg_score"] - rand, 4),
            "n_contracts":  llm["n_contracts"],
            "n_errors":     llm["n_errors"],
            **{k: llm[k] for k in COST_KEYS if k in llm},
        }
        if "avg_tokens" in llm:
            rows[level]["score_per_1k_tokens"] = round(
                1000 * llm["avg_score"] / llm["avg_tokens"], 4) if llm["avg_tokens"] else 0.0

    details = [d for level in tiers for d in real_scores[level]["details"]]
    n_total = sum(r["n_contracts"] for r in rows.values())
    overall = {
        "n_contracts":         n_total,
        "n_errors":            sum(r["n_errors"] for r in rows.values()),
        # macro = mean of tier means; micro = mean over all contracts
        "macro_avg_score":     round(sum(r["llm_score"] for r in rows.values()) / len(rows), 4),
        "micro_avg_score":     round(sum(d["score"] for d in details) / n_total, 4),
        "macro_avg_f1":        round(sum(r["f1"] for r in rows.values()) / len(rows), 4),
        "macro_random_score":  round(sum(r["random_score"] for r in rows.values()) / len(rows), 4),
    }
    overall["macro_gap_vs_random"] = round(overall["macro_avg_score"]
                                           - overall["macro_random_score"], 4)
    audits = [d["audit"] for d in details if "audit" in d]
    if audits and len(audits) == n_total:
        overall["micro_avg_tokens"]   = round(sum(a["tokens"] for a in audits) / n_total, 2)
        overall["micro_avg_cost_usd"] = round(sum(a["cost_usd"] for a in audits) / n_total, 6)
        overall["micro_avg_rounds"]   = round(sum(a["rounds"] for a in audits) / n_total, 4)
        overall["score_per_1k_tokens"] = (
            round(1000 * overall["micro_avg_score"] / overall["micro_avg_tokens"], 4)
            if overall["micro_avg_tokens"] else 0.0)
    return rows, overall


def step5_report(rows: dict, overall: dict):
    print("\n\n" + "=" * 65)
    print("FINAL RESULTS — REAL EVALUATION (held-out test split)")
    print("=" * 65)
    print(f"\n  {'Tier':<10} {'n':>3} {'Random':>8} {'LLM Score':>10} "
          f"{'Precision':>10} {'Recall':>8} {'F1':>7} {'Gap':>8}")
    print("  " + "-" * 63)
    for level, r in rows.items():
        print(f"  {level:<10} {r['n_contracts']:>3} {r['random_score']:>8.4f} "
              f"{r['llm_score']:>10.4f} {r['precision']:>10.4f} {r['recall']:>8.4f} "
              f"{r['f1']:>7.4f} {r['gap']:>+8.4f}")
    print(f"\n  Overall (macro) score: {overall['macro_avg_score']:.4f}   "
          f"(micro over {overall['n_contracts']} contracts: {overall['micro_avg_score']:.4f})")
    if "micro_avg_tokens" in overall:
        modes = {}
        for r in rows.values():
            for m, c in r.get("mode_counts", {}).items():
                modes[m] = modes.get(m, 0) + c
        print(f"  Cost: {overall['micro_avg_tokens']:.0f} tokens/contract  "
              f"(${overall['micro_avg_cost_usd']:.5f})  rounds={overall['micro_avg_rounds']:.2f}  "
              f"score/1k tokens={overall['score_per_1k_tokens']:.4f}  modes={modes}")
    if overall["n_errors"]:
        print(f"  [WARN] {overall['n_errors']} contracts hit LLM errors and scored 0 — "
              f"rerun before reporting.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--evaluator", default="basic", choices=list(EVALUATORS),
                        help="basic = single-shot JSON prompt; cot = chain-of-thought; "
                             "ba|ta|hybrid = real audit modes with n=3 consensus rounds; "
                             "rl = trained PPO mode selector + DQN stopping policy")
    parser.add_argument("--n",    type=int, default=None,
                        help="Contracts per tier (default: whole test split)")
    parser.add_argument("--seed", type=int, default=SAMPLE_SEED,
                        help="Seed for sub-sampling when --n is set")
    parser.add_argument("--tier", default="all",
                        choices=["all", *TIERS],
                        help="Which tier to evaluate (default: all)")
    parser.add_argument("--labels", default="curated", choices=list(LABEL_SETS),
                        help="curated = dataset category labels only (default); "
                             "all = also static-tool findings")
    parser.add_argument("--out",  default=None,
                        help="Results file (default depends on --evaluator)")
    parser.add_argument("--skip-download", action="store_true")
    args = parser.parse_args()

    tiers = list(TIERS) if args.tier == "all" else [args.tier]
    if args.evaluator == "basic":
        from verify import real_evaluator as ev
        evaluate, ev_config = ev.evaluate_difficulty, ev.config()
    elif args.evaluator == "cot":
        from verify import improved_evaluator as ev
        evaluate, ev_config = ev.evaluate_improved, ev.config()
    else:
        import functools
        from verify import mode_evaluator as ev
        evaluate  = functools.partial(ev.evaluate_mode, mode=args.evaluator)
        ev_config = ev.config(args.evaluator)

    if not args.skip_download:
        step1_download()

    splits = step2_split(tiers)
    rand   = step3_random_baseline(tiers, args.n, RANDOM_SEED, RANDOM_TRIALS, args.labels)
    real   = step4_real_llm(evaluate, tiers, args.n, args.seed, args.labels)
    rows, overall = build_summary(rand, real, tiers)
    step5_report(rows, overall)

    out = ROOT / (args.out or DEFAULT_OUT[args.evaluator])
    out.parent.mkdir(exist_ok=True, parents=True)
    out.write_text(json.dumps({
        "metadata": run_metadata(
            **ev_config,
            tiers         = tiers,
            labels        = args.labels,
            n             = args.n,
            sample_seed   = args.seed,
            random_seed   = RANDOM_SEED,
            random_trials = RANDOM_TRIALS,
            test_sets     = {t: sorted(splits[t]["test"]) for t in tiers},
        ),
        "random_baseline": rand,
        "summary":         rows,
        "overall":         overall,
        "results":         real,
    }, indent=2), encoding="utf-8")
    print(f"\n  Saved => {rel(out)}")
    print(f"  Verify: py verify/regrade.py {rel(out)}")


if __name__ == "__main__":
    main()
