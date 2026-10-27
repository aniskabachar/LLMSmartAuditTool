"""
auditor/environment.py

Gymnasium-style environment over the labelled SmartBugs contracts in tasks/.

One episode = one contract:
  reset()  -> observation with the contract source (labels stay hidden)
  step(a)  -> grades the agent's findings against ground truth;
              reward = grader score (0.6 * recall + 0.4 * precision)
  state()  -> current episode, for inspection or a stateless API

Follows the Gymnasium API (reset returns (obs, info); step returns
(obs, reward, terminated, truncated, info)) without depending on the
gymnasium package, because observations are source text rather than
fixed-size tensors.

Contracts are served from a seeded shuffle of the chosen tier and split,
cycling when exhausted, so a run is reproducible from (difficulty, split, seed).
"""

from __future__ import annotations
import json, random
from pathlib import Path
from typing import Any, Union

from .models import Action
from .grader import grade, LABEL_SETS

ROOT  = Path(__file__).resolve().parent.parent
TIERS = ("easy", "medium", "hard")
PARTS = ("train", "test", "all")


def _contracts(difficulty: str, split: str) -> list[Path]:
    task_dir  = ROOT / "tasks" / difficulty
    sol_files = sorted(f for f in task_dir.glob("*.sol") if f.with_suffix(".json").exists())
    if not sol_files:
        raise FileNotFoundError(f"No labelled contracts in tasks/{difficulty}/")
    if split == "all":
        return sol_files
    split_file = ROOT / "verify" / "splits" / f"{difficulty}_split.json"
    if not split_file.exists():
        raise FileNotFoundError(f"Missing verify/splits/{difficulty}_split.json "
                                f"(run: py verify/split_dataset.py)")
    stems = set(json.loads(split_file.read_text(encoding="utf-8"))[split])
    return [f for f in sol_files if f.stem in stems]


class AuditEnvironment:
    """Single-step audit episodes over real contracts with real ground truth."""

    def __init__(self, difficulty: str = "easy", split: str = "test",
                 labels: str = "curated", seed: int = 0, with_features: bool = False):
        self._configure(difficulty, split, labels, seed)
        self.with_features = with_features
        self._current: Path | None = None
        self._done = True
        self._last: dict | None = None
        self.episodes = 0
        self.total_reward = 0.0

    def _configure(self, difficulty: str, split: str, labels: str, seed: int):
        if difficulty not in TIERS:
            raise ValueError(f"difficulty must be one of {TIERS}")
        if split not in PARTS:
            raise ValueError(f"split must be one of {PARTS}")
        if labels not in LABEL_SETS:
            raise ValueError(f"labels must be one of {LABEL_SETS}")
        self.difficulty, self.split, self.labels, self.seed = difficulty, split, labels, seed
        self._order = _contracts(difficulty, split)
        random.Random(seed).shuffle(self._order)
        self._cursor = 0

    # ── Gymnasium API ────────────────────────────────────────────────────────

    def reset(self, seed: int | None = None, options: dict | None = None) -> tuple[dict, dict]:
        """
        Start an episode on the next contract.

        options may set "difficulty", "split", "labels" (reconfigures the
        environment when they differ) or "contract" (a file name in the
        current tier/split). A `seed` reshuffles the contract order.
        """
        options = options or {}
        changed = any(options.get(k) not in (None, getattr(self, k))
                      for k in ("difficulty", "split", "labels"))
        if seed is not None or changed:
            self._configure(options.get("difficulty") or self.difficulty,
                            options.get("split") or self.split,
                            options.get("labels") or self.labels,
                            self.seed if seed is None else seed)

        if options.get("contract"):
            name = Path(options["contract"]).stem
            match = [p for p in self._order if p.stem == name]
            if not match:
                raise KeyError(f"{options['contract']} is not in {self.difficulty}/{self.split}")
            self._current = match[0]
        else:
            self._current = self._order[self._cursor % len(self._order)]
            self._cursor += 1

        self._done, self._last = False, None
        return self._observation(), self._info()

    def step(self, action: Union[Action, dict]) -> tuple[dict, float, bool, bool, dict]:
        """Grade the agent's findings for the current contract and end the episode."""
        if self._current is None or self._done:
            raise RuntimeError("Call reset() before step()")
        if isinstance(action, dict):
            action = Action(**action)
        gt     = json.loads(self._current.with_suffix(".json").read_text(encoding="utf-8"))
        result = grade(action, gt, labels=self.labels)
        reward = float(result["score"])

        self._done = True
        self._last = {"reward": reward, **result}
        self.episodes += 1
        self.total_reward += reward
        return self._observation(), reward, True, False, {**self._info(), **result}

    def state(self) -> dict[str, Any]:
        return {
            **self._info(),
            "done":          self._done,
            "last_result":   self._last,
            "episodes":      self.episodes,
            "mean_reward":   round(self.total_reward / self.episodes, 4) if self.episodes else None,
            "contracts":     len(self._order),
            "cursor":        self._cursor,
        }

    # ── helpers ──────────────────────────────────────────────────────────────

    def _observation(self) -> dict:
        if self._current is None:
            return {}
        code = self._current.read_text(encoding="utf-8", errors="replace")
        obs = {"contract": self._current.name, "difficulty": self.difficulty,
               "source_code": code}
        if self.with_features:
            obs["features"] = self._features(code)
        return obs

    def _info(self) -> dict:
        return {"difficulty": self.difficulty, "split": self.split, "labels": self.labels,
                "seed": self.seed,
                "contract": self._current.name if self._current else None}

    @staticmethod
    def _features(code: str) -> dict | None:
        """RL contract features (needs numpy + gymnasium); None if unavailable."""
        try:
            from dataclasses import asdict
            from rl_environment.contract_analyzer import ContractAnalyzer
            return asdict(ContractAnalyzer(use_slither=False).analyze_contract(code))
        except Exception:
            return None
