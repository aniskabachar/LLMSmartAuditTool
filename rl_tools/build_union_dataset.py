"""
rl_tools/build_union_dataset.py

All labelled contracts with the analysers every one of them has:
  269 training contracts (combined_tools.json: 69 SmartBugs + 200 SolidiFI)
  + the 96 real-bug contracts of real_tools.json not among them
  = 365 contracts x 7 analysers:
    slither (local), llm_ba, llm_ba_med, llm_ba_dasp (gpt-oss-120b),
    llm_qwen_ba, llm_qwen_think, llm_qwen_dasp (Qwen 3.8 27B)

Purpose: the 269-contract training set is 74% SolidiFI (injected bugs), where gpt-oss is the
stronger model; on real-world bugs Qwen-DASP does better. Cross-validating on this union puts
real-bug contracts in every training fold (folds are stratified by origin and label), so it
tests whether the coordinator learns to pick the right model family per contract.

No API calls: all replies were collected earlier.
Run:  py rl_tools/build_union_dataset.py
Out:  datasets/tool_results/union_tools.json
"""

from __future__ import annotations
import json, sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TR = ROOT / "datasets" / "tool_results"
TOOLS = ["slither", "llm_ba", "llm_ba_med", "llm_ba_dasp", "llm_qwen_ba", "llm_qwen_think", "llm_qwen_dasp"]


def main():
    from rl_offline.common import _parse, utf8_console
    from rl_tools.build_dataset import LLM_TYPE_MAP
    utf8_console()
    train = json.loads((TR / "agents_live_qwen_tools.json").read_text(encoding="utf-8"))
    real = json.loads((TR / "real_tools.json").read_text(encoding="utf-8"))["contracts"]
    qwen = {t: json.loads((TR / f"{t}.json").read_text(encoding="utf-8"))["contracts"]
            for t in TOOLS if t.startswith("llm_qwen")}

    contracts = {}
    for n, c in train["contracts"].items():                       # already has all 7 analysers
        contracts[n] = {**{k: v for k, v in c.items() if k != "tools"},
                        "tools": {t: c["tools"][t] for t in TOOLS}}
    for n, c in real.items():
        if n in contracts:
            continue
        tools = {t: c["tools"][t] for t in ("slither", "llm_ba", "llm_ba_med", "llm_ba_dasp")}
        for t, rep in qwen.items():
            r = rep[n]
            a, _ = _parse(r["raw"])
            types = {v.type.value for v in a.vulnerabilities} if a else set()
            tools[t] = {"found": sorted({LLM_TYPE_MAP.get(x, x) for x in types}),
                        "seconds": float(r["seconds"]),
                        "tokens": int(r["prompt_tokens"] + r["completion_tokens"])}
        contracts[n] = {**{k: v for k, v in c.items() if k != "tools"}, "tools": tools}

    from collections import Counter
    origins = Counter(c.get("origin", "smartbugs") for c in contracts.values())
    out = TR / "union_tools.json"
    out.write_text(json.dumps({
        "source": "combined_tools.json (269) + real_tools.json held-out real contracts (96); "
                  "Slither local; gpt-oss-120b and Qwen 3.8 27B agents via Groq",
        "source_commit": train.get("source_commit"),
        "built_at": datetime.now().isoformat(timespec="seconds"),
        "tools": TOOLS, "n_contracts": len(contracts), "origins": dict(origins),
        "contracts": contracts}, indent=1), encoding="utf-8")
    print(f"Wrote {len(contracts)} contracts x {len(TOOLS)} analysers {dict(origins)} -> {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
