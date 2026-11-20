"""
rl_offline/envs.py

Gymnasium environments that replay cached real audits (rl_offline/common.py).

Utility of stopping mode M after round k on a contract:
    U = score(M, k) - lam * tokens(M, k) / 1000
score is the grader score (0.6 recall + 0.4 precision) against the curated label,
tokens are the measured tokens of all calls up to round k. lam is the price of
1k tokens in score points; sweeping it traces the cost-accuracy frontier.

StopEnv (DQN)  — one episode = one contract audited in one mode. After each
                 round the agent sees contract features, the mode, and how the
                 findings are converging, and chooses CONTINUE (0) or STOP (1).
                 Reward for CONTINUE = gain in score - lam * extra tokens / 1000.
                 Stopping after round 1 is allowed (the old policy forced >= 2).
ModeEnv (PPO)  — one episode = one contract. The agent sees the 28 contract
                 features and picks BA / TA / HYBRID; the reward is the utility of
                 that mode audited until the stopping rule (trained DQN or fixed n).
"""

from __future__ import annotations
from typing import Callable

import gymnasium as gym
import numpy as np
from gymnasium import spaces

from rl_offline.common import MAX_ROUNDS, MODES, Item, Round

N_CONTRACT_FEATURES = 28
NEW_FEATURES        = slice(22, 28)     # the 6 v2 security features (ablation: zero them)
CONTINUE, STOP      = 0, 1
TOKEN_NORM          = 40_000.0


def utility(r: Round, lam: float) -> float:
    return r.score - lam * r.tokens / 1000.0


def contract_obs(item: Item, use_new_features: bool = True) -> np.ndarray:
    f = np.asarray(item.features, dtype=np.float32).copy()
    if not use_new_features:
        f[NEW_FEATURES] = 0.0
    return f


def stop_obs(item: Item, mode: str, k: int, max_rounds: int = MAX_ROUNDS,
             convergence: bool = True, use_new_features: bool = True) -> np.ndarray:
    """State after round k (1-based) of `mode`. Uses only what a live run can observe."""
    traj = item.traj[mode]
    cur = traj[k - 1]
    prev = traj[k - 2] if k >= 2 else None
    if prev is None:
        jac, added, removed, dconf = 1.0, len(cur.types), 0, 0.0
    else:
        union = cur.types | prev.types
        jac = len(cur.types & prev.types) / len(union) if union else 1.0
        added, removed = len(cur.types - prev.types), len(prev.types - cur.types)
        dconf = cur.confidence - prev.confidence
    conv = [jac, min(added / 5, 1.0), min(removed / 5, 1.0), float(np.clip(dconf, -1, 1))]
    if not convergence:
        conv = [0.0, 0.0, 0.0, 0.0]
    mode_1h = [1.0 if m == mode else 0.0 for m in MODES]
    tail = [k / max_rounds, min(cur.tokens / TOKEN_NORM, 1.0),
            min(len(cur.action.vulnerabilities) / 10, 1.0), cur.confidence]
    return np.concatenate([contract_obs(item, use_new_features),
                           np.array(mode_1h + tail + conv, dtype=np.float32)])


STOP_OBS_DIM = N_CONTRACT_FEATURES + 3 + 4 + 4


class StopEnv(gym.Env):
    metadata = {"render_modes": []}

    def __init__(self, items: list[Item], lam: float, max_rounds: int = MAX_ROUNDS,
                 convergence: bool = True, use_new_features: bool = True,
                 mode_sampler: Callable[[Item, np.random.Generator], str] | None = None):
        super().__init__()
        self.items, self.lam, self.R = items, lam, max_rounds
        self.convergence, self.use_new_features = convergence, use_new_features
        self.mode_sampler = mode_sampler or (lambda it, rng: MODES[rng.integers(len(MODES))])
        self.observation_space = spaces.Box(-1.0, 1.0, (STOP_OBS_DIM,), np.float32)
        self.action_space = spaces.Discrete(2)

    def _obs(self):
        return stop_obs(self.item, self.mode, self.k, self.R, self.convergence,
                        self.use_new_features)

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        self.item = self.items[self.np_random.integers(len(self.items))]
        self.mode = self.mode_sampler(self.item, self.np_random)
        self.k = 1
        return self._obs(), {}

    def step(self, action):
        if int(action) == STOP:
            return self._obs(), 0.0, True, False, {"stop_round": self.k}
        traj = self.item.traj[self.mode]
        cur, nxt = traj[self.k - 1], traj[self.k]
        reward = (nxt.score - cur.score) - self.lam * (nxt.tokens - cur.tokens) / 1000.0
        self.k += 1
        done = self.k >= self.R
        return self._obs(), float(reward), done, False, {"stop_round": self.k}


class ModeEnv(gym.Env):
    """Contextual bandit over audit modes; reward table precomputed per (item, mode)."""
    metadata = {"render_modes": []}

    def __init__(self, items: list[Item], rewards: np.ndarray, use_new_features: bool = True):
        super().__init__()
        self.items, self.rewards = items, rewards          # rewards: (n_items, 3)
        self.use_new_features = use_new_features
        self.observation_space = spaces.Box(-1.0, 1.0, (N_CONTRACT_FEATURES,), np.float32)
        self.action_space = spaces.Discrete(len(MODES))

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        self.i = int(self.np_random.integers(len(self.items)))
        return contract_obs(self.items[self.i], self.use_new_features), {}

    def step(self, action):
        r = float(self.rewards[self.i, int(action)])
        return contract_obs(self.items[self.i], self.use_new_features), r, True, False, {}


# ── Applying policies to replayed trajectories ────────────────────────────────

def run_stopping(item: Item, mode: str, decide: Callable[[np.ndarray], int],
                 max_rounds: int = MAX_ROUNDS, convergence: bool = True,
                 use_new_features: bool = True) -> int:
    """Round at which a stopping policy stops `mode` on `item`."""
    k = 1
    while k < max_rounds:
        if decide(stop_obs(item, mode, k, max_rounds, convergence, use_new_features)) == STOP:
            break
        k += 1
    return k


def mode_reward_table(items: list[Item], lam: float, stop_round: Callable[[Item, str], int]) -> np.ndarray:
    return np.array([[utility(it.traj[m][stop_round(it, m) - 1], lam) for m in MODES]
                     for it in items], dtype=np.float32)
