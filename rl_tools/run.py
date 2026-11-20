"""
rl_tools/run.py

Tool-selection RL on real static-analysis results (no LLM calls).

Task: for each contract, an agent chooses which analysis tools to run, one at a time,
seeing the contract's 28 ContractAnalyzer features plus what the tools run so far
reported, and decides when to STOP. The audit report is the union of the categories
found by the tools that were run.

  score   = 0.6 * recall + 0.4 * precision   (auditor/grader formula, category level)
  utility = score - lam * (total tool run time in minutes)

Data: datasets/tool_results/curated_tools.json (rl_tools/build_dataset.py) — real
outputs and run times of 8 tools on the 69 SmartBugs curated contracts.

Methods compared (all out-of-fold):
  single tools, all tools, best fixed subset (chosen on the train fold by utility),
  greedy cheapest-first, random subset, PPO (proposed), PPO-static (ablation: does not
  see tool results, so it cannot adapt), per-contract oracle (upper bound).

Evaluation: repeated stratified K-fold cross-validation (every contract is tested by a
policy that never saw it), 95% bootstrap CIs over contracts, paired bootstrap of PPO
minus each baseline.

Run:
  py rl_tools/run.py                       # default: lam sweep, 5 folds x 3 repeats
  py rl_tools/run.py --quick               # small smoke test
Writes verify/results/tools/<timestamp>/{results.json, report.md}.
"""

from __future__ import annotations
import argparse, itertools, json, platform, random, sys, time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np
import gymnasium as gym
from gymnasium import spaces

DATA = ROOT / "datasets" / "tool_results" / "curated_tools.json"
# "compact" contract features (indices into the 28 ContractAnalyzer features): lines of code,
# external calls, handles ether, cyclomatic complexity. With only 55 training contracts per
# fold, all 28 let PPO memorise contracts (train utility above best-fixed, test far below).
COMPACT = (0, 4, 19, 27)
TYPES = ("access_control", "bad_randomness", "denial_of_service", "front_running",
         "integer_overflow", "other", "reentrancy", "timestamp_dependence",
         "unchecked_low_level_calls")


# ── data ──────────────────────────────────────────────────────────────────────

def load():
    d = json.loads(DATA.read_text(encoding="utf-8"))
    tools = d["tools"]
    items = []
    for name, c in d["contracts"].items():
        items.append({
            "name": name, "labels": frozenset(c["labels"]), "tier": c["tier"],
            "x": np.asarray(c["features"], dtype=np.float32),
            "found": [frozenset(c["tools"][t]["found"]) for t in tools],
            "sec": np.asarray([c["tools"][t]["seconds"] for t in tools], dtype=np.float32),
        })
    return d, tools, items


def score(found: frozenset, labels: frozenset) -> float:
    tp = len(found & labels)
    p = tp / len(found) if found else 0.0
    r = tp / len(labels) if labels else 0.0
    return 0.6 * r + 0.4 * p


def outcome(it, subset) -> tuple[float, float, float, float]:
    """(score, precision, recall, minutes) of running a set of tool indices."""
    found = frozenset().union(*[it["found"][i] for i in subset]) if subset else frozenset()
    tp = len(found & it["labels"])
    p = tp / len(found) if found else 0.0
    r = tp / len(it["labels"])
    return 0.6 * r + 0.4 * p, p, r, float(sum(it["sec"][i] for i in subset)) / 60.0


# ── environment ───────────────────────────────────────────────────────────────

class ToolEnv(gym.Env):
    """One episode = one contract. Actions 0..T-1 run a tool, T = STOP.
    Reward per step = gain in score - lam * minutes of the tool just run.
    Choosing a tool already run is a no-op with a small penalty (it used to end the
    episode, which taught the agent to stop early); at evaluation such actions are
    masked out. Episodes are truncated after 2*T steps."""

    REPEAT_PENALTY = 0.01

    def __init__(self, items, n_tools, lam, mu, sd, adaptive=True, seed=0, feat_idx=None):
        super().__init__()
        self.items, self.T, self.lam = items, n_tools, lam
        self.feat_idx = list(feat_idx) if feat_idx is not None else list(range(len(mu)))
        self.mu, self.sd = mu[self.feat_idx], sd[self.feat_idx]
        self.adaptive = adaptive
        self.rng = np.random.default_rng(seed)
        self.observation_space = spaces.Box(-10, 10, (len(self.feat_idx) + n_tools + len(TYPES) + 1,), np.float32)
        self.action_space = spaces.Discrete(n_tools + 1)

    def obs(self):
        used = np.zeros(self.T, np.float32)
        used[list(self.used)] = 1.0
        found = np.zeros(len(TYPES), np.float32)
        if self.adaptive:
            f = frozenset().union(*[self.it["found"][i] for i in self.used]) if self.used else frozenset()
            for k, t in enumerate(TYPES):
                found[k] = float(t in f)
        x = np.clip((self.it["x"][self.feat_idx] - self.mu) / self.sd, -10, 10)
        return np.concatenate([x, used, found, [min(self.minutes / 10.0, 1.0)]]).astype(np.float32)

    def reset(self, *, seed=None, options=None, item=None):
        super().reset(seed=seed)
        self.it = item if item is not None else self.items[self.rng.integers(len(self.items))]
        self.used, self.minutes, self.cur, self.t = [], 0.0, 0.0, 0
        return self.obs(), {}

    def step(self, a):
        a = int(a)
        self.t += 1
        if a == self.T:
            return self.obs(), 0.0, True, False, {}
        if a in self.used:
            return self.obs(), -self.REPEAT_PENALTY, False, self.t >= 2 * self.T, {}
        self.used.append(a)
        s, _, _, m = outcome(self.it, self.used)
        dm = float(self.it["sec"][a]) / 60.0
        r = (s - self.cur) - self.lam * dm
        self.cur, self.minutes = s, m
        done = len(self.used) == self.T
        return self.obs(), float(r), done, False, {}


def rollout(model, env, it) -> list[int]:
    """Greedy policy with tools already run masked out."""
    import torch
    obs, _ = env.reset(item=it)
    for _ in range(env.T + 1):
        with torch.no_grad():
            o, _ = model.policy.obs_to_tensor(obs)
            probs = model.policy.get_distribution(o).distribution.probs[0].cpu().numpy()
        probs[list(env.used)] = -1.0
        obs, _, done, trunc, _ = env.step(int(np.argmax(probs)))
        if done or trunc:
            break
    return list(env.used)


# ── baselines ─────────────────────────────────────────────────────────────────

def all_subsets(n):
    return [s for k in range(0, n + 1) for s in itertools.combinations(range(n), k)]


def best_fixed(train, lam, subsets):
    return max(subsets, key=lambda s: np.mean([outcome(it, s)[0] - lam * outcome(it, s)[3] for it in train]))


def greedy_cheapest(train, lam, n):
    """Add tools in order of mean run time while train utility improves."""
    order = sorted(range(n), key=lambda i: np.mean([it["sec"][i] for it in train]))
    cur, best_u = [], np.mean([outcome(it, [])[0] for it in train])
    for i in order:
        u = np.mean([outcome(it, cur + [i])[0] - lam * outcome(it, cur + [i])[3] for it in train])
        if u > best_u:
            cur, best_u = cur + [i], u
    return tuple(cur)


def oracle(it, lam, subsets):
    return max(subsets, key=lambda s: outcome(it, s)[0] - lam * outcome(it, s)[3])


# ── stats ─────────────────────────────────────────────────────────────────────

def boot_ci(x, n=10000, seed=0):
    x = np.asarray(x, float)
    rng = np.random.default_rng(seed)
    m = rng.choice(x, (n, len(x)), replace=True).mean(1)
    return float(x.mean()), float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))


def paired(a, b, n=10000, seed=0):
    d = np.asarray(a, float) - np.asarray(b, float)
    rng = np.random.default_rng(seed)
    m = rng.choice(d, (n, len(d)), replace=True).mean(1)
    return float(d.mean()), float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5)), float((m <= 0).mean())


def folds(items, k, seed):
    """Stratified by label: each fold gets a share of every label."""
    rng = random.Random(seed)
    by = {}
    for i, it in enumerate(items):
        by.setdefault(min(it["labels"]), []).append(i)
    out = [[] for _ in range(k)]
    j = 0
    for lab in sorted(by):
        idx = by[lab][:]
        rng.shuffle(idx)
        for i in idx:
            out[j % k].append(i)
            j += 1
    return out


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    from stable_baselines3 import PPO
    import torch
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lams", type=float, nargs="+", default=[0.0, 0.01, 0.02, 0.05])
    ap.add_argument("--main-lam", type=float, default=0.02)
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--steps", type=int, default=30_000)
    ap.add_argument("--ent", type=float, default=0.02, help="PPO entropy coefficient")
    ap.add_argument("--features", choices=("compact", "all"), default="compact",
                    help="contract features PPO sees: 4 compact ones (default) or all 28")
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args()
    if args.quick:
        args.lams, args.repeats, args.steps = [args.main_lam], 1, 3000
    if args.main_lam not in args.lams:
        args.lams.append(args.main_lam)
    torch.set_num_threads(1)

    meta_d, tools, items = load()
    fidx = COMPACT if args.features == "compact" else None
    T, N = len(tools), len(items)
    subsets = all_subsets(T)
    print(f"{N} contracts x {T} tools; lams {args.lams}; {args.folds}-fold x {args.repeats} repeats; "
          f"{args.steps:,} PPO steps per fold")

    started = time.time()
    # Checkpoint: each finished fold's picks are saved, so a restart (PC shut down,
    # sleep, Ctrl+C) skips the folds already done. Keyed by every setting that matters.
    cfg_key = json.dumps({"env": "v2-repeat-noop", "ent": args.ent, "features": args.features, "lams": sorted(args.lams), "main": args.main_lam, "folds": args.folds,
                          "repeats": args.repeats, "steps": args.steps,
                          "data": meta_d.get("source_commit"), "n": N, "tools": tools,
                          "built_at": meta_d.get("built_at")}, sort_keys=True)
    import hashlib
    ckpt = ROOT / "verify" / "results" / "tools" / "_checkpoints" /         f"{hashlib.sha256(cfg_key.encode()).hexdigest()[:12]}.json"
    ckpt.parent.mkdir(parents=True, exist_ok=True)
    done_folds = json.loads(ckpt.read_text(encoding="utf-8")) if ckpt.exists() else {}
    if done_folds:
        print(f"Resuming: {len(done_folds)} folds already done ({ckpt.relative_to(ROOT)})")
    per_lam = {}
    for lam in args.lams:
        reps = args.repeats if lam == args.main_lam else 1
        picks = {m: [[] for _ in range(N)] for m in
                 ("PPO", "PPO-static", "best-fixed", "greedy-cheapest", "random")}
        for rep in range(reps):
            for f, test_idx in enumerate(folds(items, args.folds, seed=rep)):
                key = f"{lam:g}|{rep}|{f}"
                if key in done_folds:
                    for m, per_i in done_folds[key].items():
                        for i, sub_ in per_i.items():
                            picks[m][int(i)].append(sub_)
                    continue
                fold_picks = {m: {} for m in picks}
                tr = [items[i] for i in range(N) if i not in set(test_idx)]
                X = np.stack([it["x"] for it in tr])
                mu, sd = X.mean(0), X.std(0) + 1e-6
                seed = 1000 * rep + f
                variants = ("PPO", "PPO-static") if lam == args.main_lam else ("PPO",)
                for v in variants:
                    env = ToolEnv(tr, T, lam, mu, sd, adaptive=(v == "PPO"), seed=seed, feat_idx=fidx)
                    model = PPO("MlpPolicy", env, seed=seed, verbose=0, n_steps=512, batch_size=128,
                                ent_coef=args.ent, gamma=1.0, policy_kwargs={"net_arch": [64, 64]})
                    model.learn(args.steps)
                    ev = ToolEnv(tr, T, lam, mu, sd, adaptive=(v == "PPO"), feat_idx=fidx)
                    for i in test_idx:
                        fold_picks[v][i] = [int(a) for a in rollout(model, ev, items[i])]
                bf, gc = best_fixed(tr, lam, subsets), greedy_cheapest(tr, lam, T)
                rng = random.Random(seed)
                for i in test_idx:
                    fold_picks["best-fixed"][i] = [int(a) for a in bf]
                    fold_picks["greedy-cheapest"][i] = [int(a) for a in gc]
                    fold_picks["random"][i] = rng.sample(range(T), rng.randint(1, T))
                fold_picks = {m: v for m, v in fold_picks.items() if v}
                for m, per_i in fold_picks.items():
                    for i, sub_ in per_i.items():
                        picks[m][i].append(sub_)
                done_folds[key] = {m: {str(i): v for i, v in per_i.items()} for m, per_i in fold_picks.items()}
                tmp = ckpt.with_suffix(".tmp")
                tmp.write_text(json.dumps(done_folds), encoding="utf-8")
                tmp.replace(ckpt)
                print(f"  lam={lam:<5g} rep {rep} fold {f}: best-fixed={[tools[j] for j in bf]}  "
                      f"({time.time() - started:.0f}s)", flush=True)
        # fixed, data-independent methods
        for j, t in enumerate(tools):
            picks[t] = [[[j]] for _ in range(N)]
        picks["all tools"] = [[list(range(T))] for _ in range(N)]
        picks["oracle"] = [[list(oracle(it, lam, subsets))] for it in items]
        per_lam[lam] = picks

    def summarise(picks, lam):
        rows = {}
        for m, per in picks.items():
            if not any(per):                       # method not run at this lam (ablation)
                continue
            u, s, p, r, mins, ntools = [], [], [], [], [], []
            for it, runs in zip(items, per):
                o = [outcome(it, sub) for sub in runs]       # average over repeats
                s.append(np.mean([x[0] for x in o])); p.append(np.mean([x[1] for x in o]))
                r.append(np.mean([x[2] for x in o])); mins.append(np.mean([x[3] for x in o]))
                u.append(np.mean([x[0] - lam * x[3] for x in o])); ntools.append(np.mean([len(x) for x in runs]))
            use = Counter(tools[j] for runs in per for sub in runs for j in sub)
            n_runs = sum(len(runs) for runs in per)
            rows[m] = {"utility": boot_ci(u), "score": boot_ci(s), "precision": float(np.mean(p)),
                       "recall": float(np.mean(r)), "minutes": float(np.mean(mins)),
                       "n_tools": float(np.mean(ntools)),
                       "tool_use_pct": {t: round(100 * use[t] / n_runs, 1) for t in tools},
                       "_u": u, "_s": s}
        return rows

    results = {lam: summarise(per_lam[lam], lam) for lam in args.lams}
    main_rows = results[args.main_lam]
    paired_rows = {m: {"utility": paired(main_rows["PPO"]["_u"], r["_u"]),
                       "score": paired(main_rows["PPO"]["_s"], r["_s"])}
                   for m, r in main_rows.items() if m != "PPO"}

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = ROOT / "verify" / "results" / "tools" / ts
    out.mkdir(parents=True, exist_ok=True)
    clean = {str(l): {m: {k: v for k, v in r.items() if not k.startswith("_")} for m, r in rows.items()}
             for l, rows in results.items()}
    meta = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "data": str(DATA.relative_to(ROOT)), "data_source": meta_d["source"],
            "data_commit": meta_d["source_commit"], "n_contracts": N, "tools": tools,
            "labels": dict(Counter(min(it["labels"]) for it in items)),
            "lams": args.lams, "main_lam": args.main_lam, "folds": args.folds, "repeats": args.repeats,
            "ppo_steps": args.steps, "features": args.features, "ent_coef": args.ent, "minutes": round((time.time() - started) / 60, 1),
            "python": platform.python_version()}
    (out / "results.json").write_text(json.dumps({"metadata": meta, "results": clean, "paired_vs_PPO": paired_rows},
                                                 indent=2), encoding="utf-8")

    def fmt(ci):
        return f"{ci[0]:.3f} [{ci[1]:.3f}, {ci[2]:.3f}]"
    order = ["PPO", "PPO-static", "best-fixed", "greedy-cheapest", "all tools", "random"] + list(tools) + ["oracle"]
    md = ["# Tool-selection RL on real static-analysis results", "",
          f"- Data: real outputs and run times of {T} tools on the {N} SmartBugs curated contracts "
          f"({meta_d['source']}).",
          f"- PPO sees {'4 compact contract features' if args.features == 'compact' else 'all 28 contract features'} "
          f"+ tools run so far + categories they found.",
          f"- Utility = score − λ·minutes of tool run time; λ = {args.main_lam} at the main setting.",
          f"- {args.folds}-fold stratified cross-validation × {args.repeats} repeats; every contract is "
          f"scored by policies that never saw it. 95% bootstrap CIs over contracts.", "",
          f"## Main result (λ = {args.main_lam})", "",
          "| Method | Utility [95% CI] | Score [95% CI] | Precision | Recall | Minutes | Tools run |",
          "|---|---|---|---|---|---|---|"]
    for m in order:
        r = main_rows[m]
        md.append(f"| {m} | {fmt(r['utility'])} | {fmt(r['score'])} | {r['precision']:.3f} | "
                  f"{r['recall']:.3f} | {r['minutes']:.2f} | {r['n_tools']:.2f} |")
    md += ["", "## Paired bootstrap: PPO minus method", "",
           "| Method | ΔUtility [95% CI] | p | ΔScore [95% CI] | p |", "|---|---|---|---|---|"]
    for m in order[1:]:
        u, s = paired_rows[m]["utility"], paired_rows[m]["score"]
        md.append(f"| {m} | {u[0]:+.3f} [{u[1]:+.3f}, {u[2]:+.3f}] | {u[3]:.3f} | "
                  f"{s[0]:+.3f} [{s[1]:+.3f}, {s[2]:+.3f}] | {s[3]:.3f} |")
    md += ["", "p = one-sided bootstrap probability that PPO is not better (small = PPO better).", "",
           "## Tools PPO runs (% of contracts)", "",
           "| " + " | ".join(tools) + " |", "|" + "---|" * len(tools),
           "| " + " | ".join(f"{main_rows['PPO']['tool_use_pct'][t]}" for t in tools) + " |", "",
           "## λ sweep", "", "| λ | PPO utility | PPO score | PPO minutes | best-fixed utility | best-fixed score | "
           "best-fixed minutes | oracle utility |", "|---|---|---|---|---|---|---|---|"]
    for lam in args.lams:
        r = results[lam]
        md.append(f"| {lam:g} | {r['PPO']['utility'][0]:.3f} | {r['PPO']['score'][0]:.3f} | {r['PPO']['minutes']:.2f} | "
                  f"{r['best-fixed']['utility'][0]:.3f} | {r['best-fixed']['score'][0]:.3f} | "
                  f"{r['best-fixed']['minutes']:.2f} | {r['oracle']['utility'][0]:.3f} |")
    (out / "report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))
    print(f"\nSaved => {out.relative_to(ROOT)}  ({meta['minutes']} min)")


if __name__ == "__main__":
    main()
