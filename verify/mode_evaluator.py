"""
verify/mode_evaluator.py

Real (not simulated) audit modes on labelled SmartBugs contracts, mirroring
the LLM-SmartAudit modes the RL layer chooses between:

  ba     — Broad Analysis: open-ended reasoning over every vulnerability class
  ta     — Targeted Analysis: the base system's 41 detector scenarios
           (CompanyConfig/SmartContractTA/PhaseConfig.json), checked in batches
  hybrid — BA then TA unconditionally, findings merged
  rl     — RL-Adaptive: the trained PPO mode selector picks BA/TA/Hybrid from
           contract features, and the trained DQN stopping policy decides after
           each consensus round whether to continue

Every mode runs consensus rounds: round 1 is the initial analysis, each later
round reviews the current findings against the code. Fixed modes use the base
system's fixed n=3 rounds; RL mode asks the DQN from round 2 on (max 5).

Token usage comes from the API response, so cost numbers are measured, not
modelled. RL mode needs the trained policy files; it never falls back to a
heuristic, since RL-Adaptive numbers must come from the trained policies.

Run via:
    py verify/run_real_evaluation.py --evaluator ba|ta|hybrid|rl
"""

from __future__ import annotations
import hashlib, json, os, re, sys, time
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

from pathlib import Path
from auditor.models import Action
from verify.eval_common import (
    ROOT, SAMPLE_SEED, load_contracts, select_contracts, contract_record, summarize, sha256,
)
from verify.real_evaluator import get_client, truncate, _parse_llm_response, _normalize_action

MODES = ("ba", "ta", "hybrid", "rl")

MODEL          = "openai/gpt-oss-120b"
TEMPERATURE    = 0.1
MAX_TOKENS     = 1000
MAX_CODE_CHARS = 6000
CALL_DELAY     = 8      # seconds between calls, stays within the 8000 TPM free tier
TA_BATCH       = 8      # detector scenarios per TA call (41 scenarios -> 6 calls)

FIXED_ROUNDS     = 3    # base system's fixed consensus cap (n=3)
RL_MIN_ROUNDS    = 2    # DQN is consulted from round 2 on
RL_MAX_ROUNDS    = 5    # hard cap on real LLM rounds in RL mode
TRAIN_MAX_ROUNDS = 10   # max_rounds the DQN saw in training (state normalisation)

# USD per million tokens, used to report cost and to fill the DQN's cost feature.
# Check current Groq pricing; token counts are the exact cost measure.
PRICE_PER_M_INPUT  = 0.15
PRICE_PER_M_OUTPUT = 0.75

MODE_MODEL = Path(os.getenv("RL_MODE_MODEL", ROOT / "models" / "mode_selector" / "mode_selector.zip"))
STOP_MODEL = Path(os.getenv("RL_STOP_MODEL", ROOT / "models" / "stopping_policy" / "stopping_policy.zip"))

TA_CONFIG = ROOT / "CompanyConfig" / "SmartContractTA" / "PhaseConfig.json"

# ── Prompts ───────────────────────────────────────────────────────────────────

TYPES = ("reentrancy|integer_overflow|access_control|tx_origin|timestamp_dependence|selfdestruct|"
         "uninitialized_storage|unchecked_low_level_calls|bad_randomness|front_running|"
         "denial_of_service|other")

FORMAT = f"""Respond ONLY in this exact JSON format with no extra text:
{{
  "vulnerabilities": [
    {{
      "type": "{TYPES}",
      "location": "function name or line description",
      "severity": "low|medium|high|critical",
      "explanation": "exact reason this is a vulnerability"
    }}
  ],
  "confidence": 0.0
}}
"confidence" is your confidence (0.0-1.0) that the list is complete and correct."""

BA_SYSTEM = f"""You are a precise Solidity smart contract security auditor performing a BROAD analysis.
Reason about the contract's purpose, its trust assumptions and the behaviour of every function,
and consider every class of vulnerability rather than a fixed checklist.
Report ONLY vulnerabilities clearly present in the code. Prefer fewer accurate findings over many
false positives. Check the pragma version before deciding integer overflow risk.

{FORMAT}"""

TA_SYSTEM = f"""You are a precise Solidity smart contract security auditor performing a TARGETED analysis.
Check the contract ONLY against the vulnerability scenarios you are given, applying each
scenario's checks. Report ONLY vulnerabilities clearly present in the code. Map each finding to
the closest type in the list below, using "other" when none fits.

{FORMAT}"""

REVIEW_SYSTEM = f"""You are a senior Solidity security auditor in a consensus review round.
You are given a contract and the findings from the previous round. Re-examine the code: keep
findings clearly supported by the code, remove findings that are not, and add any vulnerability
that is clearly present but was missed.

{FORMAT}"""


def load_ta_scenarios() -> list[dict]:
    """Detector scenarios from the base system's TA phase configuration."""
    phases = json.loads(TA_CONFIG.read_text(encoding="utf-8"))
    scenarios = []
    for name, phase in phases.items():
        if not name.endswith("Detector"):
            continue
        lines = [l.strip() for l in phase["phase_prompt"]]
        intro = next(l for l in lines if "particular focus on" in l)
        focus_text = intro.split("particular focus on", 1)[1].strip()
        focus_text = re.sub(r"^(identifying )?(vulnerabilities related to )?", "", focus_text)
        m = re.match(r"(.+?)\.(?:\s+(.*)|$)", focus_text)
        focus, description = (m.group(1), m.group(2) or "") if m else (focus_text, "")
        checks = [re.sub(r"^\d+\.\s*", "", l) for l in lines if re.match(r"^\d+\.", l)]
        scenarios.append({"detector": name, "focus": focus.strip(),
                          "description": description, "checks": checks})
    return scenarios


TA_SCENARIOS = load_ta_scenarios()


def _scenario_text(batch: list[dict]) -> str:
    blocks = []
    for s in batch:
        block = f"### {s['focus']}"
        if s["description"]:
            block += f"\n{s['description']}"
        block += "".join(f"\n- {c}" for c in s["checks"])
        blocks.append(block)
    return "\n\n".join(blocks)


TA_BATCH_TEXTS = [_scenario_text(TA_SCENARIOS[i:i + TA_BATCH])
                  for i in range(0, len(TA_SCENARIOS), TA_BATCH)]

# ── LLM session with token accounting ─────────────────────────────────────────

def _chat(system: str, user: str) -> tuple[str, int, int, str | None]:
    """One LLM call with rate-limit retry. Returns (text, prompt_toks, completion_toks, error)."""
    for attempt in range(4):
        try:
            r = get_client().chat.completions.create(
                model=MODEL, temperature=TEMPERATURE, max_tokens=MAX_TOKENS,
                messages=[{"role": "system", "content": system},
                          {"role": "user",   "content": user}],
            )
            time.sleep(CALL_DELAY)
            usage = getattr(r, "usage", None)
            return (r.choices[0].message.content or "",
                    getattr(usage, "prompt_tokens", 0) or 0,
                    getattr(usage, "completion_tokens", 0) or 0, None)
        except RuntimeError:
            raise   # missing API key: abort the run instead of scoring zeros
        except Exception as e:
            err = str(e)
            if "429" in err or "rate_limit" in err:
                m = re.search(r'try again in (\d+\.?\d*)s', err)
                wait = float(m.group(1)) + 2.0 if m else 15.0 * (attempt + 1)
                print(f"    Rate limit — waiting {wait:.0f}s", end=" ", flush=True)
                time.sleep(wait)
                continue
            print(f"    LLM error: {err[:120]}")
            return "", 0, 0, f"llm_error: {err[:300]}"
    return "", 0, 0, "max_retries_exceeded"


class _Session:
    """Accumulates raw responses, token usage and the first error for one contract."""

    def __init__(self):
        self.raws: list[str] = []
        self.prompt_tokens = self.completion_tokens = self.calls = 0
        self.error: str | None = None

    def ask(self, system: str, user: str) -> tuple[Action, float | None] | None:
        text, pt, ct, err = _chat(system, user)
        self.calls += 1
        self.prompt_tokens += pt
        self.completion_tokens += ct
        if err and not self.error:
            self.error = err
        if not text:
            return None
        self.raws.append(text)
        parsed = _parse_llm_response(text)
        try:
            conf = min(1.0, max(0.0, float(parsed.get("confidence"))))
        except (TypeError, ValueError):
            conf = None
        return _normalize_action(parsed), conf

    @property
    def cost_usd(self) -> float:
        return (self.prompt_tokens * PRICE_PER_M_INPUT
                + self.completion_tokens * PRICE_PER_M_OUTPUT) / 1e6


def _merge(actions: list[Action]) -> Action:
    """Union of findings, de-duplicated by (type, location)."""
    seen, vulns = set(), []
    for a in actions:
        for v in a.vulnerabilities:
            key = (v.type, v.location.strip().lower())
            if key not in seen:
                seen.add(key)
                vulns.append(v)
    return Action(vulnerabilities=vulns)


def _findings_json(action: Action) -> str:
    return json.dumps([{"type": v.type.value, "location": v.location,
                        "severity": v.severity.value, "explanation": v.explanation}
                       for v in action.vulnerabilities], indent=1)


def initial_round(code: str, mode: str, s: _Session) -> tuple[Action, float | None]:
    results = []
    if mode in ("BA", "HYBRID"):
        results.append(s.ask(BA_SYSTEM, f"Contract to audit:\n\n{code}"))
    if mode in ("TA", "HYBRID"):
        for scenarios in TA_BATCH_TEXTS:
            results.append(s.ask(TA_SYSTEM, f"Vulnerability scenarios to check:\n\n{scenarios}"
                                            f"\n\nContract to audit:\n\n{code}"))
    ok    = [r for r in results if r is not None]
    confs = [c for _, c in ok if c is not None]
    return _merge([a for a, _ in ok]), (sum(confs) / len(confs) if confs else None)


def review_round(code: str, findings: Action, s: _Session) -> tuple[Action, float | None]:
    r = s.ask(REVIEW_SYSTEM, f"Contract:\n\n{code}\n\nFindings from the previous round:\n"
                             f"{_findings_json(findings)}\n\nReturn the reviewed list.")
    return r if r is not None else (findings, None)   # failed call keeps previous findings

# ── RL policies ───────────────────────────────────────────────────────────────

class RLPolicies:
    """Trained PPO mode selector + DQN stopping policy, applied to real audit state."""

    MODE_BY_ACTION = {0: "BA", 1: "TA", 2: "HYBRID"}

    def __init__(self, mode_model, stop_model):
        self.mode_model, self.stop_model = mode_model, stop_model

    @classmethod
    def load(cls, mode_path: Path | None = None, stop_path: Path | None = None) -> "RLPolicies":
        mode_path, stop_path = Path(mode_path or MODE_MODEL), Path(stop_path or STOP_MODEL)
        missing = [str(p) for p in (mode_path, stop_path) if not p.exists()]
        if missing:
            raise FileNotFoundError(
                "Trained RL policies not found: " + ", ".join(missing) + ". Train them first "
                "(py training/train_policies.py) or set RL_MODE_MODEL / RL_STOP_MODEL. "
                "RL mode does not fall back to a heuristic.")
        try:
            from stable_baselines3 import PPO, DQN
        except ImportError as e:
            raise RuntimeError("RL mode needs torch, stable-baselines3 and gymnasium "
                               "(pip install -r verify/requirements-rl.txt)") from e
        # Custom policy classes must be importable to unpickle the saved models
        for module in ("rl_policies.mode_selector", "rl_policies.stopping_policy"):
            try:
                __import__(module)
            except Exception:
                pass
        return cls(PPO.load(str(mode_path), device="cpu"), DQN.load(str(stop_path), device="cpu"))

    @staticmethod
    def _obs(state):
        return state.to_vector().reshape(1, -1)

    def select_mode(self, features) -> str:
        from rl_environment.rl_architecture import AuditState
        action, _ = self.mode_model.predict(self._obs(AuditState(contract_features=features)),
                                            deterministic=True)
        return self.MODE_BY_ACTION[int(action.item())]   # predict returns a 1-element array

    def should_stop(self, features, rounds: int, cost_usd: float,
                    n_found: int, confidences: list[float]) -> bool:
        from rl_environment.rl_architecture import AuditState
        state = AuditState(contract_features=features, current_round=rounds,
                           max_rounds=TRAIN_MAX_ROUNDS, cumulative_cost=cost_usd,
                           vulnerabilities_found=n_found, confidence_scores=list(confidences))
        action, _ = self.stop_model.predict(self._obs(state), deterministic=True)
        return bool(int(action.item()))   # 0 = CONTINUE, 1 = STOP


_policies: RLPolicies | None = None


def get_policies() -> RLPolicies:
    global _policies
    if _policies is None:
        _policies = RLPolicies.load()
    return _policies


def contract_features(code: str):
    """28 contract features via the RL layer's analyzer (regex path, deterministic)."""
    from rl_environment.contract_analyzer import ContractAnalyzer
    return ContractAnalyzer(use_slither=False).analyze_contract(code)

# ── Audit loop ────────────────────────────────────────────────────────────────

def audit(contract_code: str, mode: str,
          policies: RLPolicies | None = None) -> tuple[Action, list[str], str | None, dict]:
    """
    Run one audit in `mode` (ba|ta|hybrid|rl).
    Returns (findings, raw responses, first error or None, audit details).
    """
    code = truncate(contract_code, MAX_CODE_CHARS)
    s = _Session()
    features = None
    if mode == "rl":
        policies = policies or get_policies()
        features = contract_features(contract_code)
        run_mode, source = policies.select_mode(features), "ppo"
    else:
        run_mode, source = mode.upper(), "fixed"

    findings, conf = initial_round(code, run_mode, s)
    confidences = [conf if conf is not None else 0.0]
    rounds = 1
    while True:
        if mode == "rl":
            if rounds >= RL_MAX_ROUNDS:
                stop_reason = "max_rounds"
                break
            if rounds >= RL_MIN_ROUNDS and policies.should_stop(
                    features, rounds, s.cost_usd, len(findings.vulnerabilities), confidences):
                stop_reason = "dqn_stop"
                break
        elif rounds >= FIXED_ROUNDS:
            stop_reason = "fixed_rounds"
            break
        findings, conf = review_round(code, findings, s)
        confidences.append(conf if conf is not None else confidences[-1])
        rounds += 1

    details = {
        "mode":              run_mode,
        "mode_source":       source,
        "rounds":            rounds,
        "stop_reason":       stop_reason,
        "confidences":       [round(c, 4) for c in confidences],
        "llm_calls":         s.calls,
        "prompt_tokens":     s.prompt_tokens,
        "completion_tokens": s.completion_tokens,
        "tokens":            s.prompt_tokens + s.completion_tokens,
        "cost_usd":          round(s.cost_usd, 6),
    }
    return findings, s.raws, s.error, details


def evaluate_mode(difficulty: str, n: int | None = None, seed: int = SAMPLE_SEED,
                  use_test_split: bool = True, labels: str = "curated",
                  mode: str = "ba") -> dict:
    """Evaluate one audit mode on the test split (all of it when n is None)."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    policies = get_policies() if mode == "rl" else None   # fail fast before any LLM call
    sample = select_contracts(load_contracts(difficulty, use_test_split), n, seed)
    print(f"  Evaluating {len(sample)} contracts in {mode.upper()} mode "
          f"({'test split' if use_test_split else 'all'})")

    details = []
    for i, sol_path in enumerate(sample):
        code = sol_path.read_text(encoding="utf-8", errors="replace")
        print(f"  [{i+1}/{len(sample)}] {sol_path.name} ... ", end="", flush=True)

        findings, raws, error, info = audit(code, mode, policies)
        record = contract_record(sol_path, findings, raws, error, labels=labels, audit=info)
        details.append(record)

        print(f"{info['mode']:<6} rounds={info['rounds']} tokens={info['tokens']:<6} "
              f"score={record['score']:.3f}" + (f"  [{error}]" if error else ""))

    return summarize(difficulty, details)


def _file_hash(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16] if path.exists() else None


def config(mode: str) -> dict:
    """Evaluator settings recorded alongside results."""
    cfg = {
        "evaluator":      mode,
        "model":          MODEL,
        "temperature":    TEMPERATURE,
        "max_tokens":     MAX_TOKENS,
        "max_code_chars": MAX_CODE_CHARS,
        "prompt_hash":    sha256(BA_SYSTEM, TA_SYSTEM, REVIEW_SYSTEM, *TA_BATCH_TEXTS),
        "ta_scenarios":   len(TA_SCENARIOS),
        "ta_batch":       TA_BATCH,
        "price_per_m_tokens": {"input": PRICE_PER_M_INPUT, "output": PRICE_PER_M_OUTPUT},
    }
    if mode == "rl":
        cfg.update({
            "rounds":          {"min": RL_MIN_ROUNDS, "max": RL_MAX_ROUNDS},
            "features":        "ContractAnalyzer regex (28 features)",
            "mode_model":      {"path": str(MODE_MODEL), "sha256": _file_hash(MODE_MODEL)},
            "stop_model":      {"path": str(STOP_MODEL), "sha256": _file_hash(STOP_MODEL)},
        })
    else:
        cfg["rounds"] = FIXED_ROUNDS
    return cfg
