"""
rl_tools/paper_eval.py

The RL coordinator against the base paper's own modes on the base paper's own benchmark
(rl_tools/build_paper_dataset.py: its labelled dataset + its CVE dataset).

1. Coordinator trained WITHOUT any contract of the paper's benchmark: the deployed setup
   (13 analysers, datasets/tool_results/agents_live_qwen_tools.json, same PPO settings as
   rl_tools/audit.py) minus every training contract whose code equals or nearly equals
   (identifier-set Jaccard >= 0.8) a benchmark contract. Three seeds.
2. Each coordinator audits every benchmark contract with the coordinator loop of audit.py;
   an analyser's result is its reply collected for exactly that request
   (datasets/tool_results/llm_*.json, slither_local.json), so every method below is scored
   on the very same replies.
3. Compared with the base paper's TA mode (6 specialists + 2 consensus rounds, n = 3) and
   BA mode (one broad call), every single analyser, and running all 13.

Score = 0.6*recall + 0.4*precision on categories (as everywhere); safe contracts (no bug)
are reported as false-alarm rates. Paired bootstrap of coordinator minus each method.

Run:  py rl_tools/paper_eval.py [--seeds 0 1 2] [--retrain]
Out:  verify/results/paper_eval/paper_eval.{md,json}, models/coordinator_paper_heldout_s<seed>/
"""

from __future__ import annotations
import argparse, json, re, sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np

TR = ROOT / "datasets" / "tool_results"
TRAIN = TR / "agents_live_qwen_tools.json"
PAPER = TR / "paper_agents_tools.json"
OUT = ROOT / "verify" / "results" / "paper_eval"
TA_MAP = {"tx_origin": "access_control", "selfdestruct": "access_control", "uninitialized_storage": "other"}


def ident_set(path: Path) -> set[str]:
    from verify.real_evaluator import strip_comments
    return set(re.findall(r"[A-Za-z_]\w+", strip_comments(path.read_text(encoding="utf-8", errors="replace"))))


def overlap(train: dict, paper: dict, thr: float = 0.8) -> dict[str, tuple[str, float]]:
    """training contract -> (most similar benchmark contract, Jaccard), for those >= thr."""
    P = {n: ident_set(ROOT / c["source"]) for n, c in paper.items()}
    out = {}
    for n, c in train.items():
        s = ident_set(ROOT / c["source"])
        best = max(((m, len(s & q) / max(len(s | q), 1)) for m, q in P.items()), key=lambda x: x[1])
        if best[1] >= thr:
            out[n] = best
    return out


def sc(found, labels):
    tp = len(set(found) & set(labels))
    p = tp / len(found) if found else 0.0
    r = tp / len(labels)
    return 0.6 * r + 0.4 * p, p, r


def main():
    from rl_offline.common import trajectory, utf8_console
    from rl_tools.audit import coordinate, train as train_coord
    from rl_tools.run import boot_ci, paired
    utf8_console()
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    ap.add_argument("--retrain", action="store_true", help="retrain even if the models exist")
    args = ap.parse_args()

    paper = json.loads(PAPER.read_text(encoding="utf-8"))
    P = paper["contracts"]
    train = json.loads(TRAIN.read_text(encoding="utf-8"))["contracts"]
    ov = overlap(train, P)
    print(f"{len(ov)} of {len(train)} training contracts overlap the paper's benchmark "
          f"(Jaccard >= 0.8) and are left out of training")

    # ── 1. coordinators trained without the benchmark ──
    import rl_tools.audit as A
    from stable_baselines3 import PPO
    coords = {}
    for s in args.seeds:
        d = ROOT / "models" / f"coordinator_paper_heldout_s{s}"
        if args.retrain or not (d / "model.zip").exists():
            A.SEED = s
            train_coord(TRAIN, model_dir=d, exclude=set(ov))
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        assert set(meta["excluded"]) == set(ov), f"{d} was trained with another exclusion list; use --retrain"
        coords[s] = (meta, PPO.load(d / "model.zip", device="cpu"))

    # ── 2. every method on every benchmark contract ──
    ta = json.loads((TR / "llm_ta.json").read_text(encoding="utf-8"))["contracts"]
    tools = paper["tools"]
    res = {}                                     # method -> name -> (found, tokens)
    for n, c in P.items():
        T = c["tools"]
        for t in tools:
            res.setdefault(f"always {t}", {})[n] = (T[t]["found"], T[t].get("tokens", 0))
        res.setdefault("all 13 analysers", {})[n] = (sorted(set().union(*[T[t]["found"] for t in tools])),
                                                     sum(T[t].get("tokens", 0) for t in tools))
        for k, label in ((3, "base TA mode (n=3)"), (1, "base TA mode, round 1")):
            r = trajectory(ta[n], "TA", max_rounds=k)[k - 1]
            res.setdefault(label, {})[n] = (sorted({TA_MAP.get(v.type.value, v.type.value)
                                                    for v in r.action.vulnerabilities}), r.tokens)
        feats = np.asarray(c["features"], dtype=np.float32)
        run = lambda t, T=T: {"categories": T[t]["found"], "seconds": T[t]["seconds"], "tokens": T[t].get("tokens", 0)}
        for s, (meta, model) in coords.items():
            found, steps = coordinate(meta, model, feats, run)
            res.setdefault(f"RL coordinator s{s}", {})[n] = (sorted(found), sum(x["tokens"] for x in steps),
                                                            [x["tool"] for x in steps])
    rl_keys = [f"RL coordinator s{s}" for s in args.seeds]
    res["RL coordinator (mean of seeds)"] = None

    def per_contract(m, names):
        if m == "RL coordinator (mean of seeds)":
            v = [[sc(res[k][n][0], P[n]["labels"]) + (res[k][n][1],) for n in names] for k in rl_keys]
            return [tuple(np.mean([x[i] for x in v], 0)) for i in range(len(names))]
        return [sc(res[m][n][0], P[n]["labels"]) + (res[m][n][1],) for n in names]

    groups = {
        "Paper labelled dataset (102 vulnerable)": [n for n in P if P[n]["origin"] == "paper_labeled"],
        "Paper CVE dataset (13)": [n for n in P if P[n]["origin"] == "paper_cve"],
        "Labelled + CVE (115)": [n for n in P if P[n]["origin"] in ("paper_labeled", "paper_cve")],
    }
    order = ["RL coordinator (mean of seeds)", *rl_keys, "base TA mode (n=3)", "base TA mode, round 1",
             "always llm_ba", *[f"always {t}" for t in tools if t != "llm_ba"], "all 13 analysers"]
    md = ["# RL coordinator vs the base paper's modes, on the base paper's benchmark", "",
          f"- Contracts: evaluation/benchmark/Labeled/proc_* (102 vulnerable, 10 types; 10 safe) and "
          f"evaluation/benchmark/cve (13). Built by rl_tools/build_paper_dataset.py.",
          f"- Coordinator: deployed setup (13 analysers, PPO, λ = {coords[args.seeds[0]][0]['lam']}), "
          f"trained on {coords[args.seeds[0]][0]['n_contracts']} contracts: the {len(train)} training "
          f"contracts minus the {len(ov)} that overlap this benchmark. Seeds {args.seeds}.",
          "- Every method is scored on the same collected replies (identical requests). "
          "'always llm_ba' is the base paper's BA mode (one broad call).",
          "- Score = 0.6·recall + 0.4·precision (category level), 95% bootstrap CI; "
          "last column: coordinator (mean of seeds) minus method, paired bootstrap, one-sided p.", ""]
    out = {"n_overlap_excluded": len(ov), "overlap": {k: list(v) for k, v in ov.items()}, "groups": {}}
    for g, names in groups.items():
        md += [f"## {g}", "", "| Method | Score [95% CI] | Precision | Recall | LLM tokens / contract | Coordinator minus method |",
               "|---|---|---|---|---|---|"]
        ref = [x[0] for x in per_contract(order[0], names)]
        out["groups"][g] = {}
        for m in order:
            v = per_contract(m, names)
            s, lo, hi = boot_ci([x[0] for x in v])
            pr, rc, tok = (float(np.mean([x[i] for x in v])) for i in (1, 2, 3))
            d = "" if m.startswith("RL") else "{:+.3f} [{:+.3f}, {:+.3f}], p={:.3f}".format(*paired(ref, [x[0] for x in v]))
            md.append(f"| {m} | {s:.3f} [{lo:.3f}, {hi:.3f}] | {pr:.3f} | {rc:.3f} | {tok:,.0f} | {d} |")
            out["groups"][g][m] = {"score": s, "ci": [lo, hi], "precision": pr, "recall": rc, "tokens": tok}
        md.append("")

    # per bug type (the paper reports detection per type)
    lab = groups["Paper labelled dataset (102 vulnerable)"]
    types = sorted({P[n]["paper_type"] for n in lab})
    cols = ["RL coordinator (mean of seeds)", "base TA mode (n=3)", "always llm_ba", "always llm_qwen_dasp", "all 13 analysers"]
    md += ["## Detection rate per bug type (labelled dataset: share of contracts whose bug category was reported)", "",
           "| Paper type | n | " + " | ".join(cols) + " |", "|---|---|" + "---|" * len(cols)]
    for t in types:
        ns = [n for n in lab if P[n]["paper_type"] == t]
        row = []
        for m in cols:
            row.append(np.mean([x[2] for x in per_contract(m, ns)]))
        md.append(f"| {t} | {len(ns)} | " + " | ".join(f"{100 * x:.0f}%" for x in row) + " |")
    md.append("")

    # safe contracts: false alarms
    safe = [n for n in P if P[n]["origin"] == "paper_safe"]
    md += ["## Safe contracts (10, no known bug): false alarms", "",
           "| Method | Contracts flagged | Categories reported / contract | LLM tokens / contract |", "|---|---|---|---|"]
    for m in order:
        if m == "RL coordinator (mean of seeds)":
            fl = np.mean([[bool(res[k][n][0]) for n in safe] for k in rl_keys])
            nc = np.mean([[len(res[k][n][0]) for n in safe] for k in rl_keys])
            tk = np.mean([[res[k][n][1] for n in safe] for k in rl_keys])
        else:
            fl = np.mean([bool(res[m][n][0]) for n in safe])
            nc = np.mean([len(res[m][n][0]) for n in safe])
            tk = np.mean([res[m][n][1] for n in safe])
        md.append(f"| {m} | {100 * fl:.0f}% | {nc:.2f} | {tk:,.0f} |")

    use = {}
    for k in rl_keys:
        for n in P:
            for t in res[k][n][2]:
                use[t] = use.get(t, 0) + 1
    md += ["", "Analysers the coordinators ran (% of contracts, all seeds): " +
           ", ".join(f"{t} {100 * c / (len(P) * len(rl_keys)):.0f}%" for t, c in sorted(use.items(), key=lambda x: -x[1]))]

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "paper_eval.md").write_text("\n".join(md), encoding="utf-8")
    out["at"] = datetime.now().isoformat(timespec="seconds")
    out["per_contract"] = {m: {n: list(v) for n, v in r.items()} for m, r in res.items() if r}
    (OUT / "paper_eval.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print("\n".join(md))


if __name__ == "__main__":
    main()
