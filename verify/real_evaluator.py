"""
verify/real_evaluator.py

Replaces test_system_simple.py accuracy computation.
Real accuracy = precision/recall against SmartBugs ground truth labels.
Uses a Groq-hosted LLM to actually read and analyse Solidity contracts.

The prompt is identical for every tier: the tier is derived from the
ground-truth category, so telling the model the tier would leak the label.
"""

from __future__ import annotations
import sys, os, json
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

from auditor.models import Action, DetectedVulnerability, VulnerabilityType, Severity
from verify.eval_common import (
    SAMPLE_SEED, load_contracts, select_contracts, contract_record, summarize, sha256,
)

_client = None


def get_client():
    """Create the Groq client on first use so grading works without a key."""
    global _client
    if _client is None:
        if not os.getenv("GROQ_API_KEY"):
            raise RuntimeError("GROQ_API_KEY not set. Add it to .env.")
        from groq import Groq
        _client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    return _client

SYSTEM_PROMPT = """You are a precise Solidity smart contract security auditor.
Find ONLY vulnerabilities CLEARLY present in the code.
Do NOT guess. Prefer fewer accurate findings over many false positives.
Do NOT default to reentrancy unless you see an external call BEFORE a state update.
Check pragma version before deciding integer overflow risk.

Respond ONLY in this exact JSON format with no extra text:
{
  "vulnerabilities": [
    {
      "type": "reentrancy|integer_overflow|access_control|tx_origin|timestamp_dependence|selfdestruct|uninitialized_storage|unchecked_low_level_calls|bad_randomness|front_running|denial_of_service|other",
      "location": "function name or line description",
      "severity": "low|medium|high|critical",
      "explanation": "exact reason this is a vulnerability"
    }
  ]
}"""

VALID_TYPES = {t.value for t in VulnerabilityType}

MODEL       = "openai/gpt-oss-120b"  # Groq-hosted model that worked in smoke test
TEMPERATURE = 0.1
MAX_TOKENS  = 600                     # reduced to stay within TPM limit
USER_PREFIX = "Contract to audit:\n\n"


def config() -> dict:
    """Evaluator settings recorded alongside results."""
    return {
        "evaluator":   "basic",
        "model":       MODEL,
        "temperature": TEMPERATURE,
        "max_tokens":  MAX_TOKENS,
        "prompt_hash": sha256(SYSTEM_PROMPT, USER_PREFIX),
    }


def truncate(code: str, max_chars: int = 6000) -> str:
    if len(code) <= max_chars:
        return code
    half = max_chars // 2
    return code[:half] + "\n\n...[truncated for length]...\n\n" + code[-half:]


def _parse_llm_response(raw: str) -> dict:
    """
    Extract JSON from LLM response with multiple fallback strategies.
    Handles: markdown fences, plain JSON, JSON embedded in prose, truncated JSON.
    """
    import re as _re

    raw = raw.strip()

    # Strategy 1: strip markdown code fences
    if "```" in raw:
        # grab content between first ``` pair
        parts = raw.split("```")
        for part in parts[1:]:
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            try:
                return json.loads(part)
            except Exception:
                pass

    # Strategy 2: direct parse
    try:
        return json.loads(raw)
    except Exception:
        pass

    # Strategy 3: find first { ... } block
    match = _re.search(r'\{.*\}', raw, _re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except Exception:
            pass

    # Strategy 4: manually extract vulnerability types from prose
    # If model returned a description instead of JSON, parse keywords
    vuln_keywords = {
        "reentrancy": "reentrancy",
        "integer overflow": "integer_overflow",
        "overflow": "integer_overflow",
        "underflow": "integer_overflow",
        "access control": "access_control",
        "tx.origin": "tx_origin",
        "timestamp": "timestamp_dependence",
        "block.timestamp": "timestamp_dependence",
        "selfdestruct": "selfdestruct",
        "suicide": "selfdestruct",
        "unchecked": "unchecked_low_level_calls",
        "low-level call": "unchecked_low_level_calls",
        "randomness": "bad_randomness",
        "front.run": "front_running",
        "front run": "front_running",
        "denial of service": "denial_of_service",
        "delegatecall": "access_control",
    }
    raw_lower = raw.lower()
    found_types = []
    for keyword, vuln_type in vuln_keywords.items():
        if keyword in raw_lower and vuln_type not in found_types:
            found_types.append(vuln_type)

    if found_types:
        return {
            "vulnerabilities": [
                {"type": t, "location": "inferred from prose",
                 "severity": "medium", "explanation": "extracted from model response"}
                for t in found_types
            ]
        }

    # Strategy 5: empty — model found nothing or returned garbage
    return {"vulnerabilities": []}


def _normalize_action(parsed: dict) -> Action:
    """Coerce parsed dict into validated Action, normalising types."""
    vulns = []
    for v in parsed.get("vulnerabilities", []):
        t = v.get("type", "other").lower().strip()
        if t not in VALID_TYPES:
            t = "other"
        s = v.get("severity", "medium").lower().strip()
        if s not in {"low", "medium", "high", "critical"}:
            s = "medium"
        vulns.append(DetectedVulnerability(
            type        = VulnerabilityType(t),
            location    = v.get("location", "unknown")[:200],
            severity    = Severity(s),
            explanation = v.get("explanation", "")[:500],
        ))
    return Action(vulnerabilities=vulns)


def audit_contract(contract_code: str) -> tuple[Action, list[str], str | None]:
    """
    Runs real Groq LLM agent on one contract.
    Returns (Action, raw responses, error message or None).
    Includes exponential backoff retry on rate limit (429).
    """
    import time as _time

    max_retries = 4
    for attempt in range(max_retries):
        try:
            response = get_client().chat.completions.create(
                model    = MODEL,
                messages = [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user",
                     "content": f"{USER_PREFIX}{truncate(contract_code)}"},
                ],
                temperature = TEMPERATURE,
                max_tokens  = MAX_TOKENS,
            )
            raw    = response.choices[0].message.content or ""
            parsed = _parse_llm_response(raw)
            # Rate limit mitigation: 2-second delay between calls
            _time.sleep(2)
            return _normalize_action(parsed), [raw], None

        except RuntimeError:
            raise   # missing API key: abort the run instead of scoring zeros

        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "rate_limit" in err_str:
                # Extract wait time from error message if available
                import re as _re
                m = _re.search(r'try again in (\d+\.?\d*)s', err_str)
                wait = float(m.group(1)) + 2.0 if m else (15.0 * (attempt + 1))
                print(f"    Rate limit — waiting {wait:.0f}s (attempt {attempt+1}/{max_retries})")
                _time.sleep(wait)
                continue
            print(f"    LLM error: {e}")
            _time.sleep(5)
            return Action(vulnerabilities=[]), [], f"llm_error: {err_str[:300]}"

    print(f"    Max retries exceeded")
    return Action(vulnerabilities=[]), [], "max_retries_exceeded"


def evaluate_difficulty(
    difficulty: str,
    n: int | None = None,
    seed: int = SAMPLE_SEED,
    use_test_split: bool = True,
    labels: str = "curated",
) -> dict:
    """
    Evaluates real LLM agent on contracts from tasks/{difficulty}/

    Parameters
    ----------
    difficulty      : "easy" | "medium" | "hard"
    n               : number of contracts (None = the whole test split)
    seed            : seed for sub-sampling when n is smaller than the split
    use_test_split  : if True, only held-out test contracts are evaluated
    labels          : "curated" (category labels) or "all" (+ tool findings)

    Returns
    -------
    dict with avg_score, avg_precision, avg_recall, per-contract details
    """
    sample = select_contracts(load_contracts(difficulty, use_test_split), n, seed)
    print(f"  Evaluating {len(sample)} contracts "
          f"({'test split' if use_test_split else 'all'})")

    details = []
    for i, sol_path in enumerate(sample):
        contract_code = sol_path.read_text(encoding="utf-8", errors="replace")

        print(f"  [{i+1}/{len(sample)}] {sol_path.name} ... ", end="", flush=True)

        action, raw, error = audit_contract(contract_code)
        record = contract_record(sol_path, action, raw, error, labels=labels)
        details.append(record)

        print(f"score={record['score']:.3f}  "
              f"TP={record['true_positives']}  "
              f"FP={record['false_positives']}  "
              f"miss={record['missed_bugs']}"
              + (f"  [{error}]" if error else ""))

    return summarize(difficulty, details)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--difficulty", default="easy",
                        choices=["easy","medium","hard"])
    parser.add_argument("--n", type=int, default=None,
                        help="Contracts to evaluate (default: whole test split)")
    parser.add_argument("--seed", type=int, default=SAMPLE_SEED)
    args = parser.parse_args()

    print(f"\nReal LLM evaluation: {args.difficulty} tier")
    result = evaluate_difficulty(args.difficulty, n=args.n, seed=args.seed)
    print(f"\nResult: score={result['avg_score']}  "
          f"P={result['avg_precision']}  R={result['avg_recall']}  "
          f"F1={result['avg_f1']}")
