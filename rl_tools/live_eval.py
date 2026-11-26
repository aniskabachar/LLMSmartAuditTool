"""
rl_tools/live_eval.py

Held-out, end-to-end test of the deployed coordinator (rl_tools/audit.py).

Contracts: the real-bug contracts of datasets/tool_results/real_tools.json that are NOT
among the 269 the coordinator was trained on (combined_tools.json). For each one, audit.py
runs live: real Slither runs and real Groq calls chosen by the coordinator. The result is
scored against the contract's label and compared, on the same contracts, with always
running one fixed analyser (their replies were collected earlier for real_tools.json).

Resumable: finished contracts are kept in verify/results/audits/live_eval.json.

Run:  py rl_tools/live_eval.py [--limit N]
Out:  verify/results/audits/live_eval.{json,md}
"""

from __future__ import annotations
import argparse, json, sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TR = ROOT / "datasets" / "tool_results"
OUT = ROOT / "verify" / "results" / "audits" / "live_eval.json"


def held_out() -> dict:
    real = json.loads((TR / "real_tools.json").read_text(encoding="utf-8"))["contracts"]
    seen = json.loads((TR / "combined_tools.json").read_text(encoding="utf-8"))["contracts"]
    return {n: c for n, c in sorted(real.items()) if n not in seen}


def main():
    from rl_offline.common import utf8_console
    from rl_tools.audit import audit
    from rl_tools.run import boot_ci, paired
    utf8_console()
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    todo_all = held_out()
    done = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    names = list(todo_all)[: args.limit] if args.limit else list(todo_all)
    todo = [n for n in names if n not in done]
    print(f"Held-out live evaluation: {len(names) - len(todo)}/{len(names)} done, {len(todo)} to go")
    for i, n in enumerate(todo, 1):
        rep = audit(ROOT / todo_all[n]["source"], quiet=True)
        errors = [s for s in rep["steps"] if "error" in s]
        if errors and "DailyLimit" in errors[0]["error"]:
            print(f"Groq daily limit reached; stopping (rerun later to continue)")
            break
        done[n] = {"found": rep["vulnerability_categories"], "analysers": rep["analysers_run"],
                   "tokens": rep["llm_tokens"], "seconds": rep["analyser_seconds"],
                   "errors": len(errors)}
        OUT.parent.mkdir(parents=True, exist_ok=True)
        tmp = OUT.with_suffix(".tmp")
        tmp.write_text(json.dumps(done, indent=1), encoding="utf-8")
        tmp.replace(OUT)
        print(f"[{i}/{len(todo)}] {n}: labels {todo_all[n]['labels']} | found {done[n]['found']} | "
              f"{'+'.join(done[n]['analysers'])} | {done[n]['tokens']:,} tok", flush=True)

    names = [n for n in names if n in done]
    if not names:
        return

    def sc(found, labels):
        tp = len(set(found) & set(labels))
        p = tp / len(found) if found else 0.0
        r = tp / len(labels) if labels else 0.0
        return 0.6 * r + 0.4 * p, p, r

    rows = {}
    rows["RL coordinator (live)"] = [(*sc(done[n]["found"], todo_all[n]["labels"]), done[n]["tokens"])
                                     for n in names]
    for t in ("slither", "llm_ba", "llm_ba_med", "llm_ba_dasp"):
        rows[f"always {t}"] = [(*sc(todo_all[n]["tools"][t]["found"], todo_all[n]["labels"]),
                                todo_all[n]["tools"][t].get("tokens", 0)) for n in names]
    allf = lambda n: set().union(*[todo_all[n]["tools"][t]["found"]
                                   for t in ("slither", "llm_ba", "llm_ba_med", "llm_ba_dasp")])
    rows["all 4 analysers"] = [(*sc(allf(n), todo_all[n]["labels"]),
                                sum(todo_all[n]["tools"][t].get("tokens", 0)
                                    for t in ("slither", "llm_ba", "llm_ba_med", "llm_ba_dasp")))
                               for n in names]

    ref = [r[0] for r in rows["RL coordinator (live)"]]
    md = ["# Held-out live evaluation of the RL coordinator", "",
          f"- {len(names)} real-bug contracts never seen in training (real_tools.json minus the 269 "
          f"training contracts); audits run live (Slither locally, LLM agents on Groq).",
          "- Fixed-analyser rows use replies collected earlier for the same contracts.",
          "- Score = 0.6·recall + 0.4·precision (category level); 95% bootstrap CIs.", "",
          "| Method | Score [95% CI] | Precision | Recall | LLM tokens / contract | Coordinator minus method |",
          "|---|---|---|---|---|---|"]
    out = {}
    for m, v in rows.items():
        s, lo, hi = boot_ci([r[0] for r in v])
        p_ = sum(r[1] for r in v) / len(v)
        r_ = sum(r[2] for r in v) / len(v)
        tok = sum(r[3] for r in v) / len(v)
        d = "" if m.startswith("RL") else "{:+.3f} [{:+.3f}, {:+.3f}], p={:.3f}".format(*paired(ref, [r[0] for r in v]))
        md.append(f"| {m} | {s:.3f} [{lo:.3f}, {hi:.3f}] | {p_:.3f} | {r_:.3f} | {tok:,.0f} | {d} |")
        out[m] = {"score": s, "ci": [lo, hi], "precision": p_, "recall": r_, "tokens": tok}
    use = {}
    for n in names:
        for t in done[n]["analysers"]:
            use[t] = use.get(t, 0) + 1
    md += ["", "Analysers the coordinator ran (% of contracts): " +
           ", ".join(f"{t} {100 * c / len(names):.0f}%" for t, c in sorted(use.items(), key=lambda x: -x[1])),
           f"Calls that failed (counted as 'found nothing'): {sum(done[n]['errors'] for n in names)}"]
    OUT.with_suffix(".md").write_text("\n".join(md), encoding="utf-8")
    OUT.with_name("live_eval_summary.json").write_text(json.dumps(
        {"n": len(names), "at": datetime.now().isoformat(timespec="seconds"), "rows": out}, indent=1),
        encoding="utf-8")
    print("\n".join(md))


if __name__ == "__main__":
    main()
