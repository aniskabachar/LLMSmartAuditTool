"""
rl_offline/check_keys.py

Sends one small audit-style request per API key in .env (Groq, NVIDIA, Cerebras)
with the collector's exact settings (model, temperature, max_tokens, reasoning_effort)
and reports whether each key works. Keys themselves are never printed.

Run:
    py rl_offline/check_keys.py                # every key
    py rl_offline/check_keys.py --provider nvidia
"""

from __future__ import annotations
import argparse, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rl_offline.llm import PROVIDERS, GroqBackend, KeyPool, load_keys

SYSTEM = ('You are a Solidity auditor. Respond ONLY with JSON: '
          '{"vulnerabilities": [{"type": "...", "location": "..."}], "confidence": 0.0}')
USER = ("pragma solidity ^0.4.24; contract W { address owner; "
        "function kill() public { require(tx.origin == owner); selfdestruct(msg.sender); } }")


def main():
    from rl_offline.common import utf8_console
    utf8_console()
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=list(PROVIDERS), default=None)
    args = ap.parse_args()
    keys = [(p, k) for p, k in load_keys() if args.provider in (None, p)]
    if not keys:
        print("No matching keys in .env.")
        return
    n_by = {}
    for prov, key in keys:
        n_by[prov] = n_by.get(prov, 0) + 1
        label = f"{prov} key {n_by[prov]}"
        b = GroqBackend(pool=KeyPool([(prov, key)]), retries=1)
        t = time.time()
        try:
            out = b._create(SYSTEM, USER)
        except Exception as e:
            print(f"{label:<16} FAILED  {str(e)[:220]}")
            continue
        raw = out["raw"].strip()
        state = "OK" if raw and out["finish_reason"] == "stop" else "WARN (empty or truncated reply)"
        print(f"{label:<16} {state}  model={b.api_model}  finish={out['finish_reason']}  "
              f"tokens={out['prompt_tokens']}+{out['completion_tokens']}  {time.time() - t:.1f}s  "
              f"reply={raw[:70]!r}")


if __name__ == "__main__":
    main()
