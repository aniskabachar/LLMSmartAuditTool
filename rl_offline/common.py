"""
rl_offline/common.py

Cache of real LLM audit calls, and replay of audit trajectories from it.

Why a cache: an audit in mode M stopped after round k is a deterministic
function of the LLM calls made up to round k (round 1 = initial analysis,
round k>1 = review of round k-1's findings). Caching every call of every mode
up to MAX_ROUNDS lets any (mode, stopping) policy be replayed exactly, with the
real findings and the real token counts, without new API calls. RL policies
train on the train-pool cache; all methods are compared on the test-split cache.

Call keys inside one contract's cache file:
    ba_r1                 BA initial analysis (1 call)
    ta_r1_b0 .. ta_r1_b5  TA initial analysis (one call per detector batch)
    {ba,ta,hybrid}_r{k}   review round k >= 2 of that mode
HYBRID round 1 is BA r1 + TA r1 merged (exactly what verify/mode_evaluator.py
does), so it reuses those calls and their token cost.

Cache files: datasets/cache/<model>/<origin>/<tier>/<stem>.json
    origin = "tasks" (tasks/, split by verify/splits) or "real" (datasets/real/, train only)
"""

from __future__ import annotations
import json, os, random, re, tempfile
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
import sys
sys.path.insert(0, str(ROOT))

from auditor.models import Action
from auditor.grader import grade
from verify.eval_common import TIERS, contracts_for, sha256

MODES      = ("BA", "TA", "HYBRID")
# 3 since the v2 collection (round 4 rarely changed the outcome in v1; saves ~15% tokens)
MAX_ROUNDS = int(os.getenv("RL_OFFLINE_MAX_ROUNDS", "3"))
# v2: max_tokens 4000 + reasoning_effort "low". datasets/cache holds the v1 collection
# (max_tokens 1000, ~43% empty replies) and is kept only for reference.
# v3 (2026-11-20): code sent to the LLM has comments stripped (verify/real_evaluator.
# strip_comments) - benchmark sources name the bug in comments. datasets/cache_v2 was
# collected from unstripped code (answers leaked) and is kept only for reference.
CACHE_ROOT = ROOT / "datasets" / "cache_v3"
REAL_DIR   = ROOT / "datasets" / "real"


def model_slug(model: str) -> str:
    return re.sub(r"[^A-Za-z0-9.-]+", "_", model)


# ── Contracts ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Contract:
    origin: str          # "tasks" | "real"
    tier:   str
    stem:   str
    part:   str          # "train" | "test"
    sol:    Path

    @property
    def id(self) -> str:
        return f"{self.origin}/{self.tier}/{self.stem}"

    @property
    def gt_path(self) -> Path:
        return self.sol.with_suffix(".json")

    def ground_truth(self) -> dict:
        return json.loads(self.gt_path.read_text(encoding="utf-8"))

    def label(self) -> str:
        return self.ground_truth()["vulnerabilities"][0]["type"]

    def code(self) -> str:
        return self.sol.read_text(encoding="utf-8", errors="replace")


def list_contracts(part: str) -> list[Contract]:
    """part = "test" (held-out tasks/ test split) | "train" (tasks/ train split + datasets/real)."""
    if part not in ("train", "test"):
        raise ValueError(part)
    out = [Contract("tasks", t, p.stem, part, p) for t in TIERS for p in contracts_for(t, part)]
    if part == "train":
        for t in TIERS:
            for p in sorted((REAL_DIR / t).glob("*.sol")):
                if p.with_suffix(".json").exists():
                    out.append(Contract("real", t, p.stem, "train", p))
    return out


def collection_order(contracts: list[Contract], seed: int = 42,
                     target: dict[str, float] | None = None) -> list[Contract]:
    """Seeded order over vulnerability types, so a partial collection stays balanced.

    Without `target`: round-robin (every type equally often).
    With `target` (type -> share, e.g. the test split's label mix): each next contract
    comes from the type furthest below its target share, so any prefix of the order
    matches that mix. Within a type, tasks/ contracts come before datasets/real ones
    (same source as the test split), each group shuffled with the seed.
    """
    rng = random.Random(seed)
    by_type: dict[str, list[Contract]] = {}
    for c in contracts:
        by_type.setdefault(c.label(), []).append(c)
    for t, lst in by_type.items():
        rng.shuffle(lst)
        lst.sort(key=lambda c: c.origin != "tasks", reverse=True)   # pop() takes tasks/ first
    order, types = [], sorted(by_type)
    if target is None:
        while any(by_type.values()):
            for t in types:
                if by_type[t]:
                    order.append(by_type[t].pop())
        return order
    floor = 0.02                                   # every type keeps a little weight
    w = {t: target.get(t, 0.0) + floor for t in types}
    tot = sum(w.values())
    w = {t: v / tot for t, v in w.items()}
    taken = {t: 0 for t in types}
    while any(by_type.values()):
        n = len(order) + 1
        t = max((t for t in types if by_type[t]), key=lambda t: (w[t] * n - taken[t], w[t]))
        order.append(by_type[t].pop())
        taken[t] += 1
    return order


def test_label_mix() -> dict[str, float]:
    """Label shares of the held-out test split (labels only; no test code reaches training)."""
    from collections import Counter
    cnt = Counter(c.label() for c in list_contracts("test"))
    n = sum(cnt.values())
    return {t: k / n for t, k in cnt.items()}


# ── Cache files ───────────────────────────────────────────────────────────────

def cache_path(c: Contract, model: str, cache_root: Path = CACHE_ROOT) -> Path:
    return cache_root / model_slug(model) / c.origin / c.tier / f"{c.stem}.json"


def load_record(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_record(path: Path, rec: dict):
    """Atomic write so an interrupted run never leaves a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, indent=1)
    for attempt in range(10):          # OneDrive/antivirus can briefly lock the target on Windows
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            import time
            time.sleep(0.5 * (attempt + 1))
    os.replace(tmp, path)


def utf8_console():
    """Avoid UnicodeEncodeError when output is redirected on Windows (cp1252)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def required_keys(n_ta_batches: int, max_rounds: int = MAX_ROUNDS) -> list[str]:
    keys = ["ba_r1"] + [f"ta_r1_b{i}" for i in range(n_ta_batches)]
    keys += [f"{m}_r{k}" for m in ("ba", "ta", "hybrid") for k in range(2, max_rounds + 1)]
    return keys


def is_complete(rec: dict | None, max_rounds: int = MAX_ROUNDS) -> bool:
    if not rec:
        return False
    return all(k in rec["calls"] for k in required_keys(rec["n_ta_batches"], max_rounds))


# ── Replay ────────────────────────────────────────────────────────────────────

def _parse(raw: str) -> tuple[Action | None, float | None]:
    """Same parsing as verify/mode_evaluator._Session.ask."""
    from verify.real_evaluator import _parse_llm_response, _normalize_action
    if not raw:
        return None, None
    parsed = _parse_llm_response(raw)
    try:
        conf = min(1.0, max(0.0, float(parsed.get("confidence"))))
    except (TypeError, ValueError):
        conf = None
    return _normalize_action(parsed), conf


def merge(actions: list[Action]) -> Action:
    """Union of findings, de-duplicated by (type, location) — as verify/mode_evaluator._merge."""
    seen, vulns = set(), []
    for a in actions:
        for v in a.vulnerabilities:
            key = (v.type, v.location.strip().lower())
            if key not in seen:
                seen.add(key)
                vulns.append(v)
    return Action(vulnerabilities=vulns)


def _tok(call: dict) -> int:
    return int(call.get("prompt_tokens", 0)) + int(call.get("completion_tokens", 0))


def _cost(call: dict) -> float:
    from verify.mode_evaluator import PRICE_PER_M_INPUT, PRICE_PER_M_OUTPUT
    return (int(call.get("prompt_tokens", 0)) * PRICE_PER_M_INPUT
            + int(call.get("completion_tokens", 0)) * PRICE_PER_M_OUTPUT) / 1e6


@dataclass
class Round:
    k:          int
    action:     Action
    types:      frozenset
    confidence: float
    tokens:     int            # cumulative tokens of this mode up to and including round k
    calls:      int            # cumulative LLM calls
    cost_usd:   float = 0.0    # cumulative cost at verify/mode_evaluator prices
    score:      float = 0.0
    precision:  float = 0.0
    recall:     float = 0.0
    f1:         float = 0.0


def initial_action(rec: dict, mode: str) -> tuple[Action, float, int, int, float]:
    """Round-1 findings, mean confidence, tokens, calls and cost for a mode."""
    calls = rec["calls"]
    keys = {"BA": ["ba_r1"],
            "TA": [f"ta_r1_b{i}" for i in range(rec["n_ta_batches"])],
            "HYBRID": ["ba_r1"] + [f"ta_r1_b{i}" for i in range(rec["n_ta_batches"])]}[mode]
    acts, confs, tokens, cost = [], [], 0, 0.0
    for k in keys:
        a, c = _parse(calls[k]["raw"])
        tokens += _tok(calls[k])
        cost += _cost(calls[k])
        if a is not None:
            acts.append(a)
            if c is not None:
                confs.append(c)
    return merge(acts), (sum(confs) / len(confs) if confs else 0.0), tokens, len(keys), cost


def trajectory(rec: dict, mode: str, gt: dict | None = None, labels: str = "curated",
               max_rounds: int = MAX_ROUNDS) -> list[Round]:
    """Rounds 1..R of `mode` that are present in the cache (stops at the first missing call)."""
    action, conf, tokens, ncalls, cost = initial_action(rec, mode)
    rounds = [Round(1, action, frozenset(v.type.value for v in action.vulnerabilities),
                    conf, tokens, ncalls, cost)]
    for k in range(2, max_rounds + 1):
        call = rec["calls"].get(f"{mode.lower()}_r{k}")
        if call is None:
            break
        a, c = _parse(call["raw"])
        prev = rounds[-1]
        if a is None:                       # empty reply keeps previous findings (as live)
            a, c = prev.action, None
        rounds.append(Round(k, a, frozenset(v.type.value for v in a.vulnerabilities),
                            c if c is not None else prev.confidence,
                            prev.tokens + _tok(call), prev.calls + 1,
                            prev.cost_usd + _cost(call)))
    if gt is not None:
        for r in rounds:
            g = grade(r.action, gt, labels=labels)
            r.score, r.precision, r.recall, r.f1 = g["score"], g["precision"], g["recall"], g["f1"]
    return rounds


def review_input_action(rec: dict, mode: str, k: int) -> Action:
    """Findings a live run would send into review round k (= findings after round k-1)."""
    traj = trajectory(rec, mode, max_rounds=k - 1)
    if len(traj) < k - 1:
        raise KeyError(f"{mode} round {k - 1} missing from cache")
    return traj[k - 2].action


# ── Loaded dataset for training/evaluation ────────────────────────────────────

@dataclass
class Item:
    """One fully cached contract: features + replayable trajectories for every mode."""
    contract: Contract
    features: "object"                       # np.ndarray (28,)
    traj:     dict = field(default_factory=dict)   # mode -> list[Round]

    @property
    def id(self) -> str:
        return self.contract.id


@lru_cache(maxsize=None)
def _features(sol: Path):
    from rl_environment.contract_analyzer import ContractAnalyzer
    import logging
    logging.getLogger().setLevel(logging.ERROR)
    code = sol.read_text(encoding="utf-8", errors="replace")
    return ContractAnalyzer(use_slither=False).analyze_contract(code).to_vector()


def load_items(part: str, model: str, cache_root: Path = CACHE_ROOT, labels: str = "curated",
               max_rounds: int = MAX_ROUNDS, verbose: bool = True) -> list[Item]:
    """All contracts of `part` whose cache is complete up to max_rounds."""
    items, missing = [], 0
    for c in list_contracts(part):
        rec = load_record(cache_path(c, model, cache_root))
        if not is_complete(rec, max_rounds):
            missing += 1
            continue
        gt = c.ground_truth()
        items.append(Item(c, _features(c.sol),
                          {m: trajectory(rec, m, gt, labels, max_rounds) for m in MODES}))
    if verbose:
        print(f"  {part}: {len(items)} contracts fully cached ({missing} not yet)")
    return items


def cache_backend(cache_root: Path, model: str) -> set[str]:
    """Backends ("groq", "mock") present in a cache directory."""
    seen = set()
    for p in (cache_root / model_slug(model)).rglob("*.json"):
        try:
            seen.add(json.loads(p.read_text(encoding="utf-8")).get("backend", "groq"))
        except Exception:
            pass
        if len(seen) > 1:
            break
    return seen
