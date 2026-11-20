"""
rl_offline/live.py

Live (real API) audits with the offline-trained policies, to confirm that the
replayed results hold on fresh LLM calls:

    py verify/run_real_evaluation.py --evaluator rl2
    set RL2_POLICY_DIR=models/offline/<tag>/lam_0.005/seed_0   (Windows; default: newest tag)

Same loop as verify/mode_evaluator.audit: PPO picks the mode from the 28
contract features, round 1 runs, then after every round the DQN sees the same
state it was trained on (contract features, mode, round, tokens, findings,
confidence, convergence) and decides whether to run another review round.
"""

from __future__ import annotations
import os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from verify import mode_evaluator as ME
from verify.eval_common import SAMPLE_SEED, contract_record, load_contracts, select_contracts, summarize
from verify.real_evaluator import truncate
from rl_offline.common import MAX_ROUNDS, MODES, Item, Round
from rl_offline.envs import STOP, contract_obs, stop_obs


def policy_dir() -> Path:
    env = os.getenv("RL2_POLICY_DIR")
    if env:
        p = Path(env)
        return p if p.is_absolute() else ROOT / p
    import json
    tags = sorted((ROOT / "models" / "offline").glob("*/training_info.json"),
                  key=lambda p: p.stat().st_mtime)
    tags = [t for t in tags if "MOCK" not in t.parent.name]
    if not tags:
        raise FileNotFoundError("No trained offline policies. Run: py rl_offline/train.py")
    info = json.loads(tags[-1].read_text(encoding="utf-8"))
    return tags[-1].parent / f"lam_{info['main_lam']:g}" / f"seed_{info['seeds'][0]}"


_models = None


def models():
    global _models
    if _models is None:
        from stable_baselines3 import DQN, PPO
        d = policy_dir()
        _models = (PPO.load(str(d / "full_mode.zip"), device="cpu"),
                   DQN.load(str(d / "full_stop.zip"), device="cpu"), d)
    return _models


def audit(contract_code: str):
    ppo, dqn, d = models()
    features = ME.contract_features(contract_code).to_vector()
    item = Item(contract=None, features=features, traj={})
    mode = MODES[int(ppo.predict(contract_obs(item), deterministic=True)[0])]

    code = truncate(contract_code, ME.MAX_CODE_CHARS)
    s = ME._Session()
    findings, conf = ME.initial_round(code, mode, s)
    rounds = [Round(1, findings, frozenset(v.type.value for v in findings.vulnerabilities),
                    conf or 0.0, s.prompt_tokens + s.completion_tokens, s.calls)]
    stop_reason = "max_rounds"
    while len(rounds) < MAX_ROUNDS:
        item.traj[mode] = rounds
        obs = stop_obs(item, mode, len(rounds), MAX_ROUNDS)
        if int(dqn.predict(obs, deterministic=True)[0]) == STOP:
            stop_reason = "dqn_stop"
            break
        findings, c = ME.review_round(code, findings, s)
        rounds.append(Round(len(rounds) + 1, findings,
                            frozenset(v.type.value for v in findings.vulnerabilities),
                            c if c is not None else rounds[-1].confidence,
                            s.prompt_tokens + s.completion_tokens, s.calls))
    details = {"mode": mode, "mode_source": "ppo_offline", "rounds": len(rounds),
               "stop_reason": stop_reason, "confidences": [round(r.confidence, 4) for r in rounds],
               "llm_calls": s.calls, "prompt_tokens": s.prompt_tokens,
               "completion_tokens": s.completion_tokens,
               "tokens": s.prompt_tokens + s.completion_tokens, "cost_usd": round(s.cost_usd, 6)}
    return findings, s.raws, s.error, details


def evaluate(difficulty: str, n: int | None = None, seed: int = SAMPLE_SEED,
             use_test_split: bool = True, labels: str = "curated") -> dict:
    models()   # fail fast before any LLM call
    sample = select_contracts(load_contracts(difficulty, use_test_split), n, seed)
    print(f"  Evaluating {len(sample)} contracts with offline PPO+DQN ({policy_dir()})")
    details = []
    for i, p in enumerate(sample):
        print(f"  [{i+1}/{len(sample)}] {p.name} ... ", end="", flush=True)
        findings, raws, err, info = audit(p.read_text(encoding="utf-8", errors="replace"))
        rec = contract_record(p, findings, raws, err, labels=labels, audit=info)
        details.append(rec)
        print(f"{info['mode']:<6} rounds={info['rounds']} tokens={info['tokens']:<6} "
              f"score={rec['score']:.3f}" + (f"  [{err}]" if err else ""))
    return summarize(difficulty, details)


def config() -> dict:
    import hashlib
    d = policy_dir()
    h = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()[:16] if p.exists() else None
    cfg = ME.config("ba")
    cfg.update({"evaluator": "rl2", "rounds": {"min": 1, "max": MAX_ROUNDS},
                "policy_dir": str(d), "mode_model_sha256": h(d / "full_mode.zip"),
                "stop_model_sha256": h(d / "full_stop.zip"),
                "training": "offline on cached real audits (rl_offline/train.py)"})
    return cfg
