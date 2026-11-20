"""
rl_offline/evaluate.py

Compares the trained policies with baselines on the HELD-OUT TEST split, by
replaying the cached real audits (exactly what a live run with the same LLM
replies would produce — see rl_offline/common.py). No API calls.

Methods (each picks a mode and a stopping round per contract):
  fixed M@n         every mode M in {BA, TA, HYBRID} with fixed n = 1..R rounds
  random@3          uniformly random mode, n=3 (exact expectation over the 3 modes)
  heuristic@3       complexity rule from the README (simple->TA, complex->BA, else HYBRID)
  best-fixed+DQN    the mode with the best TRAIN utility, stopped by the DQN
  classifier+DQN    logistic regression (train split) predicting the best mode, + DQN
  linucb+DQN        LinUCB contextual bandit trained on the train split, + DQN
  PPO+DQN           the proposed method (trained PPO mode selector + DQN stopping)
  ablations         PPO@3 (no DQN), random+DQN (no PPO), PPO+DQN w/o convergence
                    features, PPO+DQN w/o the 6 v2 features
  oracle            best mode (and round) per contract in hindsight — the upper bound

Utility = score - lam * tokens/1000 (lam = price of 1k tokens in score points).
Statistics: 95% bootstrap CIs over test contracts; paired bootstrap of
PPO+DQN minus each baseline; seed-dependent methods are averaged over seeds
per contract, and the spread across seeds is reported.

Run:
    py rl_offline/evaluate.py --tag <training tag>
Writes verify/results/offline/<timestamp>_<tag>/{results.json, report.md, pareto.png}.
"""

from __future__ import annotations
import argparse, json, sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np

from verify import mode_evaluator as ME
from verify.eval_common import git_commit
from rl_offline.common import CACHE_ROOT, MAX_ROUNDS, MODES, cache_backend, load_items
from rl_offline.envs import contract_obs, mode_reward_table, run_stopping, utility

B_BOOT = 10_000
FIXED_N = 3

# A selection: per test item, a list of (mode, round, weight) — weights sum to 1


def metrics(items, sel, lam) -> dict:
    """Per-item expected metrics under a selection (weighted over outcomes)."""
    keys = ("score", "precision", "recall", "f1", "tokens", "cost_usd", "k", "U")
    per = {k: np.zeros(len(items)) for k in keys}
    modes = Counter()
    for i, (it, choice) in enumerate(zip(items, sel)):
        for m, k, w in choice:
            r = it.traj[m][k - 1]
            for key, val in (("score", r.score), ("precision", r.precision), ("recall", r.recall),
                             ("f1", r.f1), ("tokens", r.tokens), ("cost_usd", r.cost_usd),
                             ("k", k), ("U", utility(r, lam))):
                per[key][i] += w * val
            modes[m] += w
    return {"per_item": per, "modes": {m: round(modes[m], 2) for m in MODES}}


def summarize(items, res) -> dict:
    p = res["per_item"]
    out = {k: round(float(p[k].mean()), 6 if k == "cost_usd" else 4) for k in p}
    out["rounds"] = out.pop("k")
    out["utility"] = out.pop("U")
    out["modes"] = res["modes"]
    out["by_tier"] = {}
    for tier in ("easy", "medium", "hard"):
        idx = [i for i, it in enumerate(items) if it.contract.tier == tier]
        if idx:
            out["by_tier"][tier] = {"n": len(idx), "score": round(float(p["score"][idx].mean()), 4),
                                    "tokens": round(float(p["tokens"][idx].mean()), 1)}
    return out


def boot_ci(x: np.ndarray, rng) -> list[float]:
    idx = rng.integers(0, len(x), (B_BOOT, len(x)))
    m = x[idx].mean(1)
    return [round(float(np.percentile(m, 2.5)), 4), round(float(np.percentile(m, 97.5)), 4)]


def paired(a: np.ndarray, b: np.ndarray, rng) -> dict:
    d = a - b
    idx = rng.integers(0, len(d), (B_BOOT, len(d)))
    m = d[idx].mean(1)
    return {"mean_diff": round(float(d.mean()), 4),
            "ci95": [round(float(np.percentile(m, 2.5)), 4), round(float(np.percentile(m, 97.5)), 4)],
            "p_one_sided": round(float((m <= 0).mean()), 4)}


# ── Policies ──────────────────────────────────────────────────────────────────

def load_models(d: Path, variant: str):
    from stable_baselines3 import DQN, PPO
    stop = d / f"{variant}_stop.zip"
    mode = d / f"{variant}_mode.zip"
    return (DQN.load(str(stop), device="cpu") if stop.exists() else None,
            PPO.load(str(mode), device="cpu") if mode.exists() else None)


def dqn_rounds(items, dqn, conv=True, feats=True) -> dict:
    decide = lambda obs: int(dqn.predict(obs, deterministic=True)[0])
    return {(it.id, m): run_stopping(it, m, decide, MAX_ROUNDS, conv, feats)
            for it in items for m in MODES}


def ppo_modes(items, ppo, feats=True) -> list[str]:
    return [MODES[int(ppo.predict(contract_obs(it, feats), deterministic=True)[0])] for it in items]


def complexity(it) -> float:
    f = it.features
    return float(np.mean([f[0], f[1], f[4], f[27]]))   # LOC, functions, external calls, cyclomatic


def heuristic_mode(it) -> str:
    c = complexity(it)
    return "TA" if c < 0.3 else "BA" if c > 0.7 else "HYBRID"


def linucb(train_items, rewards, alpha=0.5, epochs=5, seed=0):
    d = len(train_items[0].features) + 1
    A = [np.eye(d) for _ in MODES]
    b = [np.zeros(d) for _ in MODES]
    rng = np.random.default_rng(seed)
    X = [np.append(np.asarray(it.features, float), 1.0) for it in train_items]
    for _ in range(epochs):
        for i in rng.permutation(len(train_items)):
            x = X[i]
            ucb = []
            for a in range(len(MODES)):
                Ai = np.linalg.inv(A[a]); th = Ai @ b[a]
                ucb.append(th @ x + alpha * np.sqrt(x @ Ai @ x))
            a = int(np.argmax(ucb))
            A[a] += np.outer(x, x); b[a] += rewards[i, a] * x
    thetas = [np.linalg.inv(A[a]) @ b[a] for a in range(len(MODES))]
    return lambda it: MODES[int(np.argmax([t @ np.append(np.asarray(it.features, float), 1.0)
                                           for t in thetas]))]


def classifier(train_items, rewards, seed=0):
    from sklearn.linear_model import LogisticRegression
    X = np.array([it.features for it in train_items])
    y = rewards.argmax(1)
    if len(set(y)) == 1:
        only = MODES[int(y[0])]
        return lambda it: only
    clf = LogisticRegression(max_iter=5000, random_state=seed).fit(X, y)
    return lambda it: MODES[int(clf.predict(np.asarray(it.features).reshape(1, -1))[0])]


def sel_modes_rounds(items, modes, rounds) -> list:
    return [[(m, rounds[(it.id, m)] if isinstance(rounds, dict) else rounds, 1.0)]
            for it, m in zip(items, modes)]


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    from rl_offline.common import utf8_console
    utf8_console()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tag", required=True, help="Training tag under models/offline/")
    ap.add_argument("--labels", choices=("curated", "all"), default="curated",
                    help="Ground truth for the score: curated label only, or + tool findings")
    ap.add_argument("--cache-root", default=str(CACHE_ROOT))
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    cache_root = Path(args.cache_root)
    if not cache_root.is_absolute():
        cache_root = ROOT / cache_root
    mroot = ROOT / "models" / "offline" / args.tag
    tinfo = json.loads((mroot / "training_info.json").read_text(encoding="utf-8"))
    lams, main_lam, seeds = tinfo["lams"], tinfo["main_lam"], tinfo["seeds"]
    backends = cache_backend(cache_root, ME.MODEL)
    mock = "mock" in backends or "mock" in tinfo["backend"]
    rng = np.random.default_rng(12345)

    print("Loading caches ...")
    test = load_items("test", ME.MODEL, cache_root, labels=args.labels)
    train = load_items("train", ME.MODEL, cache_root, labels=args.labels)
    if not test:
        raise SystemExit("No fully cached test contracts. Run: py rl_offline/collect.py --part test")
    R = MAX_ROUNDS

    def lamdir(lam, seed):
        return mroot / f"lam_{lam:g}" / f"seed_{seed}"

    results, sels = {}, {}

    def add(name, sel, group, lam=main_lam, seed_sels=None):
        """Register a method. seed_sels: list of per-seed selections (averaged per item)."""
        if seed_sels is not None:
            per_seed = [summarize(test, metrics(test, s, lam)) for s in seed_sels]
            merged = [[(m, k, w / len(seed_sels)) for s in seed_sels for (m, k, w) in s[i]]
                      for i in range(len(test))]
            sel = merged
        res = metrics(test, sel, lam)
        s = summarize(test, res)
        s["group"] = group
        s["ci95"] = {k: boot_ci(res["per_item"][k], rng) for k in ("score", "tokens", "U")}
        if seed_sels is not None:
            s["seed_std"] = {k: round(float(np.std([ps[k] for ps in per_seed])), 4)
                             for k in ("score", "tokens", "utility")}
            s["n_seeds"] = len(seed_sels)
        results[name], sels[name] = s, res
        return s

    # Fixed configurations
    for m in MODES:
        for n in range(1, R + 1):
            add(f"{m}@{n}", [[(m, n, 1.0)] for _ in test], "fixed")
    add("random@3", [[(m, FIXED_N, 1 / 3) for m in MODES] for _ in test], "baseline")
    add("heuristic@3", [[(heuristic_mode(it), FIXED_N, 1.0)] for it in test], "baseline")

    # Best fixed configuration chosen on TRAIN (no test peeking)
    if train:
        tr_u = {(m, n): np.mean([utility(it.traj[m][n - 1], main_lam) for it in train])
                for m in MODES for n in range(1, R + 1)}
        bf_cfg = max(tr_u, key=tr_u.get)
    else:
        bf_cfg = max(((m, n) for m in MODES for n in range(1, R + 1)),
                     key=lambda c: results[f"{c[0]}@{c[1]}"]["utility"])
    best_fixed_name = f"{bf_cfg[0]}@{bf_cfg[1]}"
    best_fixed_mode3 = max(MODES, key=lambda m: np.mean([utility(it.traj[m][FIXED_N - 1], main_lam)
                                                         for it in (train or test)]))

    # Seed-dependent methods at the main lambda
    groups: dict[str, list] = {k: [] for k in (
        "PPO+DQN", "PPO@3 (no DQN)", "random+DQN (no PPO)", "PPO+DQN w/o convergence feats",
        "PPO+DQN w/o v2 features", "best-fixed-mode+DQN", "classifier+DQN", "linucb+DQN",
        "oracle-mode+DQN")}
    stop_tables = {}
    for seed in seeds:
        d = lamdir(main_lam, seed)
        dqn, ppo = load_models(d, "full")
        rounds = dqn_rounds(test, dqn)
        stop_tables[seed] = rounds
        groups["PPO+DQN"].append(sel_modes_rounds(test, ppo_modes(test, ppo), rounds))
        groups["random+DQN (no PPO)"].append(
            [[(m, rounds[(it.id, m)], 1 / 3) for m in MODES] for it in test])
        best = [max(MODES, key=lambda m: utility(it.traj[m][rounds[(it.id, m)] - 1], main_lam))
                for it in test]
        groups["oracle-mode+DQN"].append(sel_modes_rounds(test, best, rounds))
        if train:
            tr_rounds = dqn_rounds(train, dqn)
            tr_rew = mode_reward_table(train, main_lam, lambda it, m: tr_rounds[(it.id, m)])
            bm = MODES[int(tr_rew.mean(0).argmax())]
            groups["best-fixed-mode+DQN"].append(sel_modes_rounds(test, [bm] * len(test), rounds))
            clf = classifier(train, tr_rew, seed)
            groups["classifier+DQN"].append(sel_modes_rounds(test, [clf(it) for it in test], rounds))
            lin = linucb(train, tr_rew, seed=seed)
            groups["linucb+DQN"].append(sel_modes_rounds(test, [lin(it) for it in test], rounds))
        _, ppo_fs = load_models(d, "fixed_stop")
        if ppo_fs is not None:
            groups["PPO@3 (no DQN)"].append(sel_modes_rounds(test, ppo_modes(test, ppo_fs), FIXED_N))
        dqn_nc, ppo_nc = load_models(d, "no_conv")
        if dqn_nc is not None:
            groups["PPO+DQN w/o convergence feats"].append(
                sel_modes_rounds(test, ppo_modes(test, ppo_nc), dqn_rounds(test, dqn_nc, conv=False)))
        dqn_22, ppo_22 = load_models(d, "feat22")
        if dqn_22 is not None:
            groups["PPO+DQN w/o v2 features"].append(
                sel_modes_rounds(test, ppo_modes(test, ppo_22, feats=False),
                                 dqn_rounds(test, dqn_22, feats=False)))
    group_kind = {"PPO+DQN": "proposed", "best-fixed-mode+DQN": "baseline",
                  "classifier+DQN": "baseline", "linucb+DQN": "baseline",
                  "oracle-mode+DQN": "oracle"}
    for name, ss in groups.items():
        if ss:
            add(name, None, group_kind.get(name, "ablation"), seed_sels=ss)

    # Oracles (no seeds)
    add("oracle-mode@3", [[(max(MODES, key=lambda m: utility(it.traj[m][FIXED_N - 1], main_lam)),
                            FIXED_N, 1.0)] for it in test], "oracle")
    add("oracle (mode+round)", [[max(((m, k, 1.0) for m in MODES for k in range(1, R + 1)),
                                     key=lambda c: utility(it.traj[c[0]][c[1] - 1], main_lam))]
                                for it in test], "oracle")

    # Paired comparisons: PPO+DQN vs every non-oracle method
    prop = sels["PPO+DQN"]["per_item"]
    comparisons = {}
    for name, res in sels.items():
        if name == "PPO+DQN":
            continue
        comparisons[name] = {k: paired(prop[k], res["per_item"][k], rng)
                             for k in ("U", "score", "tokens")}

    bf, rl = results[best_fixed_name], results["PPO+DQN"]
    orc = results["oracle (mode+round)"]
    gap = orc["utility"] - bf["utility"]
    headline = {
        "lam": main_lam,
        "best_fixed_config_chosen_on_train": best_fixed_name,
        "best_fixed_mode_at_3": best_fixed_mode3,
        "oracle_gap_closed": round((rl["utility"] - bf["utility"]) / gap, 3) if gap > 1e-9 else None,
        "token_change_vs_best_fixed_pct": round(100 * (rl["tokens"] / bf["tokens"] - 1), 1) if bf["tokens"] else None,
        "score_change_vs_best_fixed": round(rl["score"] - bf["score"], 4),
        "token_change_vs_hybrid3_pct": round(100 * (rl["tokens"] / results["HYBRID@3"]["tokens"] - 1), 1),
        "score_change_vs_hybrid3": round(rl["score"] - results["HYBRID@3"]["score"], 4),
    }

    # Stopping analysis: DQN vs fixed n per mode (seed-averaged)
    stopping = {}
    for m in MODES:
        ks = [np.mean([stop_tables[s][(it.id, m)] for s in seeds]) for it in test]
        dq = [[[(m, stop_tables[s][(it.id, m)], 1.0)] for it in test] for s in seeds]
        merged = [[(mm, k, w / len(dq)) for s in dq for (mm, k, w) in s[i]] for i in range(len(test))]
        r = summarize(test, metrics(test, merged, main_lam))
        stopping[m] = {"dqn": {"score": r["score"], "tokens": r["tokens"], "mean_round": round(float(np.mean(ks)), 2),
                               "round_hist": dict(sorted(Counter(int(round(k)) for k in ks).items()))},
                       **{f"fixed@{n}": {"score": results[f"{m}@{n}"]["score"],
                                         "tokens": results[f"{m}@{n}"]["tokens"]} for n in range(1, R + 1)}}

    # Lambda sweep: PPO+DQN frontier
    sweep = []
    for lam in lams:
        ss = []
        for seed in seeds:
            d = lamdir(lam, seed)
            if not (d / "full_mode.zip").exists():
                continue
            dqn, ppo = load_models(d, "full")
            ss.append(sel_modes_rounds(test, ppo_modes(test, ppo), dqn_rounds(test, dqn)))
        if not ss:
            continue
        merged = [[(m, k, w / len(ss)) for s in ss for (m, k, w) in s[i]] for i in range(len(test))]
        r = summarize(test, metrics(test, merged, lam))
        orc_l = summarize(test, metrics(test, [[max(((m, k, 1.0) for m in MODES for k in range(1, R + 1)),
                                                     key=lambda c: utility(it.traj[c[0]][c[1] - 1], lam))]
                                               for it in test], lam))
        cheaper = [(n, results[n]["score"]) for n in results
                   if results[n]["group"] == "fixed" and results[n]["tokens"] <= r["tokens"]]
        best_cheaper = max(cheaper, key=lambda x: x[1]) if cheaper else (None, None)
        dominated = [n for n in results if results[n]["group"] == "fixed"
                     and results[n]["tokens"] >= r["tokens"] and results[n]["score"] <= r["score"]]
        sweep.append({"lam": lam, "score": r["score"], "tokens": r["tokens"], "modes": r["modes"],
                      "rounds": r["rounds"], "oracle_score": orc_l["score"], "oracle_tokens": orc_l["tokens"],
                      "best_fixed_at_same_or_lower_cost": best_cheaper[0],
                      "its_score": best_cheaper[1], "fixed_configs_dominated": dominated})

    # Output
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = Path(args.out) if args.out else ROOT / "verify" / "results" / "offline" / f"{ts}_{args.tag}_{args.labels}"
    out.mkdir(parents=True, exist_ok=True)
    meta = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "MOCK_DATA": mock, "git_commit": git_commit(), "llm_model": ME.MODEL,
            "cache_root": str(cache_root), "training_tag": args.tag, "labels": args.labels,
            "n_test": len(test), "n_train": len(train), "max_rounds": R, "fixed_n": FIXED_N,
            "lams": lams, "main_lam": main_lam, "seeds": seeds, "bootstrap": B_BOOT,
            "test_by_tier": dict(Counter(it.contract.tier for it in test))}
    payload = {"metadata": meta, "headline": headline, "methods": results,
               "paired_vs_PPO+DQN": comparisons, "stopping": stopping, "lambda_sweep": sweep,
               "per_contract": {name: {it.id: {"score": float(res["per_item"]["score"][i]),
                                               "tokens": float(res["per_item"]["tokens"][i])}
                                       for i, it in enumerate(test)}
                                for name, res in sels.items()}}
    (out / "results.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    plot(results, sweep, out / "pareto.png", mock)
    (out / "report.md").write_text(report(payload), encoding="utf-8")
    print((out / "report.md").read_text(encoding="utf-8"))
    print(f"\nSaved => {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out}")


def plot(results, sweep, path: Path, mock: bool):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(9.5, 5), dpi=150)
    colors = {"BA": "#1f77b4", "TA": "#ff7f0e", "HYBRID": "#2ca02c"}
    for m in MODES:
        pts = sorted((v["tokens"], v["score"], n) for n, v in results.items()
                     if v["group"] == "fixed" and n.startswith(m + "@"))
        ax.plot([p[0] for p in pts], [p[1] for p in pts], "o--", color=colors[m], alpha=0.7,
                label=f"{m} fixed n=1..{len(pts)}", ms=5)
        for x, y, n in pts:
            ax.annotate(n.split("@")[1], (x, y), textcoords="offset points", xytext=(4, 3), fontsize=7,
                        color=colors[m])
    if sweep:
        sw = sorted(sweep, key=lambda s: s["tokens"])
        ax.plot([s["tokens"] for s in sw], [s["score"] for s in sw], "s-", color="#d62728", lw=2,
                ms=7, label="PPO+DQN (λ sweep)")
        for s in sw:
            ax.annotate(f"λ={s['lam']:g}", (s["tokens"], s["score"]), textcoords="offset points",
                        xytext=(5, -10), fontsize=7, color="#d62728")
    for name, mk in (("random@3", "x"), ("heuristic@3", "^")):
        if name in results:
            ax.scatter(results[name]["tokens"], results[name]["score"], marker=mk, color="gray",
                       s=50, label=name, zorder=3)
    o = results.get("oracle (mode+round)")
    if o:
        ax.scatter(o["tokens"], o["score"], marker="*", s=160, color="black", label="oracle", zorder=4)
    ax.set_xlabel("Mean tokens per contract (measured)")
    ax.set_ylabel("Mean score (0.6·recall + 0.4·precision)")
    ax.set_title(("MOCK DATA — pipeline test only\n" if mock else "") +
                 "Cost–accuracy on held-out test contracts")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=7, loc="upper left", bbox_to_anchor=(1.01, 1.0))
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def _row(name, r):
    ci = r.get("ci95", {})
    sd = r.get("seed_std")
    f = lambda k: f"{r[k]:.3f}"
    s = f"| {name} | {f('score')} [{ci['score'][0]:.3f}, {ci['score'][1]:.3f}] | {f('precision')} | {f('recall')} | " \
        f"{r['tokens']:,.0f} | {r['rounds']:.2f} | {r['utility']:.3f} [{ci['U'][0]:.3f}, {ci['U'][1]:.3f}] |"
    s += f" ±{sd['score']:.3f} |" if sd else " – |"
    return s


def report(p) -> str:
    md, h, res, meta = [], p["headline"], p["methods"], p["metadata"]
    if meta["MOCK_DATA"]:
        md += ["# ⚠ MOCK DATA — pipeline test only. Do NOT report these numbers.", ""]
    md += ["# Offline RL evaluation on real cached audits", "",
           f"- Test contracts: **{meta['n_test']}** {meta['test_by_tier']} (held-out split); "
           f"policies trained on **{meta['n_train']}** train-pool contracts.",
           f"- Ground truth: **{meta.get('labels', 'curated')}** labels"
           + (" (curated label + tool findings)" if meta.get("labels") == "all" else " (curated label only)") + ".",
           f"- LLM: `{meta['llm_model']}`; max rounds {meta['max_rounds']}; λ = {meta['main_lam']} "
           f"(utility = score − λ·tokens/1000); seeds {meta['seeds']}; {meta['bootstrap']:,} bootstrap resamples.",
           "", "## Headline", "",
           f"- Best fixed configuration (chosen on train): **{h['best_fixed_config_chosen_on_train']}**",
           f"- PPO+DQN vs best fixed: score {h['score_change_vs_best_fixed']:+.3f}, tokens "
           f"{h['token_change_vs_best_fixed_pct']:+.1f}%",
           f"- PPO+DQN vs HYBRID@3 (base system's most thorough mode): score {h['score_change_vs_hybrid3']:+.3f}, "
           f"tokens {h['token_change_vs_hybrid3_pct']:+.1f}%",
           f"- Oracle gap closed (utility): **{h['oracle_gap_closed']}** "
           f"(0 = best fixed, 1 = per-contract oracle)", ""]
    hdr = ["| Method | Score [95% CI] | Precision | Recall | Tokens | Rounds | Utility [95% CI] | Seed SD |",
           "|---|---|---|---|---|---|---|---|"]
    for title, groups in (("Proposed and baselines", ("proposed", "baseline")),
                          ("Ablations", ("proposed", "ablation")),
                          ("Fixed configurations", ("fixed",)), ("Oracles (upper bounds)", ("oracle",))):
        md += [f"## {title}", ""] + hdr
        for n, r in res.items():
            if r["group"] in groups:
                md.append(_row(n, r))
        md.append("")
    md += ["## Paired bootstrap: PPO+DQN minus method", "",
           "| Method | ΔUtility [95% CI] | p | ΔScore [95% CI] | p | ΔTokens |", "|---|---|---|---|---|---|"]
    for n, c in p["paired_vs_PPO+DQN"].items():
        if res[n]["group"] == "fixed" and not n.endswith("@3"):
            continue
        u, s, t = c["U"], c["score"], c["tokens"]
        md.append(f"| {n} | {u['mean_diff']:+.3f} [{u['ci95'][0]:+.3f}, {u['ci95'][1]:+.3f}] | {u['p_one_sided']:.3f} | "
                  f"{s['mean_diff']:+.3f} [{s['ci95'][0]:+.3f}, {s['ci95'][1]:+.3f}] | {s['p_one_sided']:.3f} | {t['mean_diff']:+,.0f} |")
    md += ["", "p = one-sided bootstrap probability that PPO+DQN is not better (small = PPO+DQN better).", "",
           "## DQN stopping vs fixed rounds (per mode)", "",
           "| Mode | DQN score | DQN tokens | mean stop round | " +
           " | ".join(f"fixed@{n} score / tokens" for n in range(1, meta["max_rounds"] + 1)) + " |",
           "|---|---|---|---|" + "---|" * meta["max_rounds"]]
    for m, s in p["stopping"].items():
        md.append(f"| {m} | {s['dqn']['score']:.3f} | {s['dqn']['tokens']:,.0f} | {s['dqn']['mean_round']} | " +
                  " | ".join(f"{s[f'fixed@{n}']['score']:.3f} / {s[f'fixed@{n}']['tokens']:,.0f}"
                             for n in range(1, meta["max_rounds"] + 1)) + " |")
    md += ["", "## λ sweep (cost–accuracy frontier)", "",
           "| λ | PPO+DQN score | tokens | modes | best fixed at ≤ same tokens | its score | fixed configs dominated |",
           "|---|---|---|---|---|---|---|"]
    for s in p["lambda_sweep"]:
        its = "–" if s["its_score"] is None else f"{s['its_score']:.3f}"
        md.append(f"| {s['lam']:g} | {s['score']:.3f} | {s['tokens']:,.0f} | {s['modes']} | "
                  f"{s['best_fixed_at_same_or_lower_cost']} | {its} | "
                  f"{len(s['fixed_configs_dominated'])} |")
    md += ["", "![Pareto](pareto.png)", ""]
    return "\n".join(md)


if __name__ == "__main__":
    main()
