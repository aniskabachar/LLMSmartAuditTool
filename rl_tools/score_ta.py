"""
rl_tools/score_ta.py

Scores the base project's TA mode (rl_tools/collect_ta.py) on the tool dataset and compares
it with the PPO tool selector of a finished rl_tools/run.py, on exactly the same contracts:

  TA@1  round 1 only (union of the 6 targeted scenario batches)
  TA@3  after the 2 consensus review rounds (the base system's fixed n = 3)
  BA@1  the base paper's BA mode (llm_ba tool, one call)
  PPO   out-of-fold picks of the run (from its checkpoint), averaged over repeats

Same scoring as everywhere (0.6 recall + 0.4 precision on categories), paired bootstrap of
PPO minus each mode, split by source, plus tokens and time per contract.

Run:  py rl_tools/score_ta.py [--run verify/results/tools/<ts>] [--lam 0.02]
Writes <run>/ta_comparison.md and .json. Exits quietly if TA collection is incomplete.
"""

from __future__ import annotations
import argparse, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np

import os
TA_FILE = Path(os.getenv("SCORE_TA_FILE", ROOT / "datasets" / "tool_results" / "llm_ta.json"))
MAP = {"tx_origin": "access_control", "selfdestruct": "access_control", "uninitialized_storage": "other"}


def main():
    import rl_tools.run as R
    from rl_offline.common import trajectory, utf8_console
    utf8_console()
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default=None, help="results folder of the PPO run (default: latest with by_origin)")
    ap.add_argument("--lam", type=float, default=0.02)
    args = ap.parse_args()

    runs = sorted((ROOT / "verify" / "results" / "tools").glob("2*"))
    run = Path(args.run) if args.run else next(
        r for r in reversed(runs) if "by_origin" in json.loads((r / "results.json").read_text(encoding="utf-8")))
    res = json.loads((run / "results.json").read_text(encoding="utf-8"))
    meta = res["metadata"]
    data = ROOT / meta["data"]
    d_meta, tools, items = R.load(data)
    N = len(items)

    ta = json.loads(TA_FILE.read_text(encoding="utf-8"))["contracts"] if TA_FILE.exists() else {}
    keys = json.loads(TA_FILE.read_text(encoding="utf-8"))["keys"] if TA_FILE.exists() else []
    missing = [it["name"] for it in items if not all(k in ta.get(it["name"], {}).get("calls", {}) for k in keys)]
    if missing:
        print(f"[score_ta] TA mode still missing for {len(missing)}/{N} contracts; nothing to score yet.")
        return

    # PPO out-of-fold picks from the run's checkpoint (same settings key as rl_tools/run.py)
    ckpts = list((ROOT / "verify" / "results" / "tools" / "_checkpoints").glob("*.json"))
    picks = None
    for ck in ckpts:
        dct = json.loads(ck.read_text(encoding="utf-8"))
        ks = [k for k in dct if k.startswith(f"{args.lam:g}|")]
        if ks and "PPO" in dct[ks[0]] and len(ks) == meta["folds"] * meta["repeats"] \
                and sum(len(dct[k]["PPO"]) for k in ks) == N * meta["repeats"] \
                and all(int(i) < N for k in ks for i in dct[k]["PPO"]):
            picks = [[] for _ in range(N)]
            for k in ks:
                for i, sub in dct[k]["PPO"].items():
                    picks[int(i)].append(sub)
            break
    if picks is None:
        raise SystemExit("[score_ta] could not find the PPO checkpoint for that run")

    def ta_round(it, k):
        rec = ta[it["name"]]
        traj = trajectory(rec, "TA", max_rounds=k)
        r = traj[k - 1]
        found = frozenset(MAP.get(v.type.value, v.type.value) for v in r.action.vulnerabilities)
        calls = [c for key, c in rec["calls"].items()
                 if key.startswith("ta_r1_b") or (key.startswith("ta_r") and int(key[4:]) <= k)]
        return found, r.tokens, sum(c["seconds"] for c in calls) / 60

    def stats(found, it):
        tp = len(found & it["labels"])
        p = tp / len(found) if found else 0.0
        rc = tp / len(it["labels"])
        return 0.6 * rc + 0.4 * p, p, rc

    rows = {}
    for name, fn in (("TA@1 (base TA mode, round 1)", lambda it: ta_round(it, 1)),
                     ("TA@3 (base TA mode, n=3)", lambda it: ta_round(it, 3))):
        s, p, rc, tok, mins = [], [], [], [], []
        for it in items:
            f, t, m = fn(it)
            a = stats(f, it)
            s.append(a[0]); p.append(a[1]); rc.append(a[2]); tok.append(t); mins.append(m)
        rows[name] = {"s": s, "p": p, "r": rc, "tok": tok, "min": mins}
    L = tools.index("llm_ba")
    rows["BA@1 (base BA mode, 1 call)"] = {
        "s": [stats(it["found"][L], it)[0] for it in items], "p": [stats(it["found"][L], it)[1] for it in items],
        "r": [stats(it["found"][L], it)[2] for it in items],
        "tok": [json.loads(data.read_text(encoding="utf-8"))["contracts"][it["name"]]["tools"]["llm_ba"]["tokens"]
                for it in items],
        "min": [float(it["sec"][L]) / 60 for it in items]}
    cdata = json.loads(data.read_text(encoding="utf-8"))["contracts"]
    ppo = {"s": [], "p": [], "r": [], "tok": [], "min": []}
    for it, runs_ in zip(items, picks):
        o = [R.outcome(it, sub) for sub in runs_]
        ppo["s"].append(np.mean([x[0] for x in o])); ppo["p"].append(np.mean([x[1] for x in o]))
        ppo["r"].append(np.mean([x[2] for x in o])); ppo["min"].append(np.mean([x[3] for x in o]))
        ppo["tok"].append(np.mean([sum(cdata[it["name"]]["tools"][tools[j]].get("tokens", 0) for j in sub)
                                   for sub in runs_]))
    rows["PPO tool selector (this project)"] = ppo

    origins = sorted({it["origin"] for it in items})
    out = {"run": str(run.relative_to(ROOT)), "lam": args.lam, "n": N, "methods": {}}
    md = ["# Base TA mode vs PPO tool selector (same contracts)", "",
          f"- Contracts: {N} from `{meta['data']}`; PPO run: `{run.relative_to(ROOT)}` (out-of-fold picks, λ = {args.lam}).",
          f"- TA mode: base project prompts (6 targeted scenario batches + consensus reviews), gpt-oss-120b, "
          f"comments stripped and benchmark bug names neutralised.", "",
          "| Method | Score [95% CI] | Precision | Recall | LLM tokens / contract | Minutes / contract |",
          "|---|---|---|---|---|---|"]
    for m, r in rows.items():
        ci = R.boot_ci(r["s"])
        out["methods"][m] = {"score": ci, "precision": float(np.mean(r["p"])), "recall": float(np.mean(r["r"])),
                             "tokens": float(np.mean(r["tok"])), "minutes": float(np.mean(r["min"])),
                             "by_origin": {o: float(np.mean([r["s"][i] for i, it in enumerate(items)
                                                             if it["origin"] == o])) for o in origins}}
        md.append(f"| {m} | {ci[0]:.3f} [{ci[1]:.3f}, {ci[2]:.3f}] | {np.mean(r['p']):.3f} | {np.mean(r['r']):.3f} | "
                  f"{np.mean(r['tok']):,.0f} | {np.mean(r['min']):.2f} |")
    md += ["", "## PPO minus each base mode (paired bootstrap over contracts)", "",
           "| Base mode | ΔScore [95% CI] | p (PPO not better) | " + " | ".join(f"ΔScore {o}" for o in origins) + " |",
           "|---|---|---|" + "---|" * len(origins)]
    for m in [k for k in rows if not k.startswith("PPO")]:
        dlt = R.paired(ppo["s"], rows[m]["s"])
        per = [R.paired([ppo["s"][i] for i, it in enumerate(items) if it["origin"] == o],
                        [rows[m]["s"][i] for i, it in enumerate(items) if it["origin"] == o]) for o in origins]
        out["methods"][m]["ppo_minus"] = {"all": dlt, **{o: x for o, x in zip(origins, per)}}
        md.append(f"| {m} | {dlt[0]:+.3f} [{dlt[1]:+.3f}, {dlt[2]:+.3f}] | {dlt[3]:.3f} | " +
                  " | ".join(f"{x[0]:+.3f} [{x[1]:+.3f}, {x[2]:+.3f}]" for x in per) + " |")
    md += ["", "## Score by source", "", "| Method | " + " | ".join(origins) + " |", "|---|" + "---|" * len(origins)]
    for m in rows:
        md.append(f"| {m} | " + " | ".join(f"{out['methods'][m]['by_origin'][o]:.3f}" for o in origins) + " |")
    (run / "ta_comparison.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    (run / "ta_comparison.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))
    print(f"\n[score_ta] Saved => {(run / 'ta_comparison.md').relative_to(ROOT)}")


if __name__ == "__main__":
    main()
