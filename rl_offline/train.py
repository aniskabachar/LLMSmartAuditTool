"""
rl_offline/train.py

Trains the DQN stopping policy and the PPO mode selector on REAL audit outcomes
replayed from the train-pool cache (tasks/ train split + datasets/real).
No API calls; the test split is never loaded here.

Per lambda (price of 1k tokens in score points) and seed:
  1. DQN  on StopEnv  (mode drawn uniformly, so it learns to stop in every mode)
  2. PPO  on ModeEnv  with rewards = utility of each mode audited until the DQN stops
Ablation variants (at --main-lam only):
  no_conv    DQN without the convergence features (+ PPO on its rewards)
  fixed_stop PPO trained against fixed n=3 rounds (no DQN)
  feat22     both policies without the 6 v2 security features

Outputs models/offline/<tag>/lam_<lam>/seed_<s>/<variant>_{stop,mode}.zip and
models/offline/<tag>/training_info.json (cache, data sizes, settings, hashes).

Run:
    py rl_offline/train.py                                  # default lambda sweep, 5 seeds
    py rl_offline/train.py --lams 0.005 --seeds 0 1 2 --tag quick
    py rl_offline/train.py --cache-root datasets/cache_mock --allow-mock --tag mock
"""

from __future__ import annotations
import argparse, hashlib, json, platform, random, sys, time
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np
import torch

from verify import mode_evaluator as ME
from rl_offline.common import CACHE_ROOT, MAX_ROUNDS, MODES, cache_backend, load_items
from rl_offline.envs import (ModeEnv, StopEnv, mode_reward_table, run_stopping)

DEFAULT_LAMS = (0.0, 0.0025, 0.005, 0.01, 0.02, 0.04)
MAIN_LAM     = 0.005
FIXED_N      = 3          # base system's fixed consensus rounds
VARIANTS     = ("full", "no_conv", "fixed_stop", "feat22")


def seed_all(seed: int):
    random.seed(seed); np.random.seed(seed); torch.manual_seed(seed)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def train_dqn(items, lam, seed, timesteps, convergence=True, use_new_features=True):
    from stable_baselines3 import DQN
    seed_all(seed)
    env = StopEnv(items, lam, convergence=convergence, use_new_features=use_new_features)
    env.reset(seed=seed)
    model = DQN("MlpPolicy", env, seed=seed, verbose=0, gamma=1.0, learning_rate=5e-4,
                buffer_size=50_000, learning_starts=1_000, batch_size=64, train_freq=4,
                target_update_interval=500, exploration_fraction=0.3,
                exploration_final_eps=0.05, policy_kwargs={"net_arch": [64, 64]}, device="cpu")
    model.learn(total_timesteps=timesteps)
    return model


def train_ppo(items, rewards, seed, timesteps, use_new_features=True):
    from stable_baselines3 import PPO
    seed_all(seed)
    env = ModeEnv(items, rewards, use_new_features=use_new_features)
    env.reset(seed=seed)
    model = PPO("MlpPolicy", env, seed=seed, verbose=0, n_steps=256, batch_size=64,
                n_epochs=10, learning_rate=3e-4, gamma=1.0, ent_coef=0.01,
                policy_kwargs={"net_arch": [64, 64]}, device="cpu")
    model.learn(total_timesteps=timesteps)
    return model


def dqn_decider(model):
    return lambda obs: int(model.predict(obs, deterministic=True)[0])


def train_variant(variant, items, lam, seed, args, out: Path) -> dict:
    t0 = time.time()
    conv, feats = variant != "no_conv", variant != "feat22"
    info = {"variant": variant, "lam": lam, "seed": seed}
    if variant == "fixed_stop":
        stop_round = lambda it, m: FIXED_N
    else:
        dqn = train_dqn(items, lam, seed, args.dqn_steps, conv, feats)
        p = out / f"{variant}_stop.zip"; dqn.save(p)
        info["stop_model"] = {"path": p.relative_to(ROOT).as_posix(), "sha256": sha(p)}
        decide = dqn_decider(dqn)
        stop_round = lambda it, m: run_stopping(it, m, decide, MAX_ROUNDS, conv, feats)
    rewards = mode_reward_table(items, lam, stop_round)
    ppo = train_ppo(items, rewards, seed, args.ppo_steps, feats)
    p = out / f"{variant}_mode.zip"; ppo.save(p)
    info["mode_model"] = {"path": p.relative_to(ROOT).as_posix(), "sha256": sha(p)}
    # Train-set diagnostics (in-sample; test numbers come from rl_offline/evaluate.py)
    from rl_offline.envs import contract_obs
    picks = [int(ppo.predict(contract_obs(it, feats), deterministic=True)[0]) for it in items]
    info["train_mode_counts"] = {MODES[a]: picks.count(a) for a in range(3)}
    info["train_utility"] = round(float(np.mean([rewards[i, a] for i, a in enumerate(picks)])), 4)
    info["train_best_fixed_utility"] = round(float(rewards.mean(0).max()), 4)
    info["train_oracle_utility"] = round(float(rewards.max(1).mean()), 4)
    info["seconds"] = round(time.time() - t0, 1)
    return info


def main():
    from rl_offline.common import utf8_console
    utf8_console()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cache-root", default=str(CACHE_ROOT))
    ap.add_argument("--lams", type=float, nargs="+", default=list(DEFAULT_LAMS))
    ap.add_argument("--main-lam", type=float, default=MAIN_LAM)
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2, 3, 4])
    ap.add_argument("--dqn-steps", type=int, default=30_000)
    ap.add_argument("--ppo-steps", type=int, default=20_000)
    ap.add_argument("--variants", nargs="+", default=list(VARIANTS), choices=VARIANTS)
    ap.add_argument("--tag", default=None)
    ap.add_argument("--labels", choices=("curated", "all"), default="curated",
                    help="Ground truth for the score: curated label only, or + tool findings")
    ap.add_argument("--allow-mock", action="store_true")
    args = ap.parse_args()

    cache_root = Path(args.cache_root)
    if not cache_root.is_absolute():
        cache_root = ROOT / cache_root
    backends = cache_backend(cache_root, ME.MODEL)
    if "mock" in backends and not args.allow_mock:
        raise SystemExit("This cache holds MOCK data. Pass --allow-mock to train on it (testing only).")
    if args.main_lam not in args.lams:
        args.lams.append(args.main_lam)
    tag = args.tag or datetime.now().strftime("%Y%m%d-%H%M") + ("-MOCK" if "mock" in backends else "")
    root = ROOT / "models" / "offline" / tag

    print("Loading train-pool cache ...")
    items = load_items("train", ME.MODEL, cache_root, labels=args.labels)
    if len(items) < 20:
        raise SystemExit(f"Only {len(items)} fully cached train contracts; collect more first "
                         f"(py rl_offline/collect.py --part train).")

    runs, started = [], time.time()
    for lam in args.lams:
        for seed in args.seeds:
            out = root / f"lam_{lam:g}" / f"seed_{seed}"
            out.mkdir(parents=True, exist_ok=True)
            variants = args.variants if lam == args.main_lam else ["full"]
            for v in variants:
                info = train_variant(v, items, lam, seed, args, out)
                runs.append(info)
                print(f"  lam={lam:<7g} seed={seed} {v:<10} modes={info['train_mode_counts']} "
                      f"U_train={info['train_utility']:.4f} (best fixed {info['train_best_fixed_utility']:.4f}, "
                      f"oracle {info['train_oracle_utility']:.4f})  {info['seconds']}s")

    meta = {
        "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "data": "real LLM audit outcomes replayed from cache (train pool only)"
                if "mock" not in backends else "MOCK cache — pipeline test only, not results",
        "backend": sorted(backends), "cache_root": str(cache_root), "labels": args.labels, "llm_model": ME.MODEL,
        "n_train_contracts": len(items),
        "train_by_origin": {o: sum(1 for i in items if i.contract.origin == o) for o in ("tasks", "real")},
        "max_rounds": MAX_ROUNDS, "fixed_n": FIXED_N, "lams": args.lams, "main_lam": args.main_lam,
        "seeds": args.seeds, "dqn_steps": args.dqn_steps, "ppo_steps": args.ppo_steps,
        "total_minutes": round((time.time() - started) / 60, 1),
        "versions": {"python": platform.python_version(),
                     **{p: version(p) for p in ("torch", "stable-baselines3", "gymnasium", "numpy")}},
        "runs": runs,
    }
    (root / "training_info.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"\nSaved {len(runs)} policy sets under {root.relative_to(ROOT)}  "
          f"({meta['total_minutes']} min)")
    print(f"Next: py rl_offline/evaluate.py --tag {tag}")


if __name__ == "__main__":
    main()
