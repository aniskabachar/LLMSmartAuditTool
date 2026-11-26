"""
rl_tools/build_agents_dataset.py

Multi-agent version of the tool-selection dataset: instead of static tools, the RL
coordinator chooses which LLM *agents* to consult for each contract.

  spec_0 .. spec_5   the base project's six TA specialist agents (one targeted detector
                     batch each: integer overflow, selfdestruct, uninitialised return,
                     centralisation, signature verification, denial of service, ...),
                     taken from their round-1 replies in datasets/tool_results/llm_ta.json
  llm_ba, llm_ba_med, llm_ba_dasp   the three generalist auditor agents (BA prompt; low
                     reasoning, medium reasoning, DASP-guided), from combined_tools.json

The base TA mode always consults all six specialists (+ two consensus reviews). The
reviews are not offered as agents: their replies were produced after seeing every
specialist's findings, so they would not be valid once the coordinator skips some.

With --hybrid the four static tools of combined_tools.json (slither, mythril, oyente,
smartcheck) are kept as extra, non-LLM agents.

No API calls: everything comes from replies already collected.

With --live the agents are joined by Slither run locally (rl_tools/run_slither.py):
exactly the tools rl_tools/audit.py can run on a new contract.

With --qwen the three Qwen 3.8 27B agents (a second model family) are added.

Run:  py rl_tools/build_agents_dataset.py [--hybrid | --live] [--qwen]
Out:  datasets/tool_results/agents_tools.json   (or agents_hybrid_tools.json, agents_live_tools.json)
"""

from __future__ import annotations
import argparse, json, sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TR = ROOT / "datasets" / "tool_results"
GENERALISTS = ["llm_ba", "llm_ba_med", "llm_ba_dasp"]
QWEN_AGENTS = ["llm_qwen_ba", "llm_qwen_think", "llm_qwen_dasp"]   # second model family (Qwen 3.8 27B)


def main():
    from rl_offline.common import _parse, utf8_console
    from rl_tools.build_dataset import LLM_TYPE_MAP
    utf8_console()
    ap = argparse.ArgumentParser()
    ap.add_argument("--hybrid", action="store_true", help="also keep the 4 static tools")
    ap.add_argument("--live", action="store_true",
                    help="deployable set: the 9 agents + Slither run locally (slither_local.json), "
                         "i.e. exactly the tools audit.py can run on a new contract")
    ap.add_argument("--qwen", action="store_true",
                    help="add the 3 Qwen agents (rl_tools/collect_llm.py --variant qwen_*)")
    args = ap.parse_args()
    qwen = {}
    if args.qwen:
        for t in QWEN_AGENTS:
            f = TR / f"{t}.json"
            qwen[t] = json.loads(f.read_text(encoding="utf-8"))["contracts"] if f.exists() else {}
    local = {}
    if args.live:
        from rl_tools.build_dataset import CATEGORY_MAP, load_mapping
        mapping = load_mapping(ROOT / "datasets" / "smartbugs_meta")
        sl = json.loads((TR / "slither_local.json").read_text(encoding="utf-8"))["contracts"]
        for n, r in sl.items():
            cats = {mapping.get(("slither", chk.strip().lower())) for chk in r["checks"]}
            local[n] = {"found": sorted({CATEGORY_MAP[c] for c in cats if c in CATEGORY_MAP}),
                        "seconds": float(r["seconds"]), "compiled": bool(r["ok"])}

    base = json.loads((TR / "combined_tools.json").read_text(encoding="utf-8"))
    ta = json.loads((TR / "llm_ta.json").read_text(encoding="utf-8"))
    n_spec = len([k for k in ta["keys"] if k.startswith("ta_r1_b")])
    specs = [f"spec_{i}" for i in range(n_spec)]
    static = [t for t in base["tools"] if not t.startswith("llm_")] if args.hybrid else []
    tools = specs + GENERALISTS + (QWEN_AGENTS if args.qwen else []) + static + (["slither"] if args.live else [])
    for t, rep in qwen.items():
        gap = [n for n in base["contracts"] if n not in rep]
        if gap:
            raise SystemExit(f"{t}: no reply for {len(gap)} contracts; run "
                             f"py rl_tools/collect_llm.py --variant {t.removeprefix('llm_')} "
                             "--data datasets/tool_results/combined_tools.json")
    if args.live:
        gap = [n for n in base["contracts"] if n not in local]
        if gap:
            raise SystemExit(f"local Slither missing for {len(gap)} contracts; run "
                             "py rl_tools/run_slither.py --data datasets/tool_results/combined_tools.json")

    empty, contracts = 0, {}
    for name, c in base["contracts"].items():
        calls = ta["contracts"][name]["calls"]
        new = {k: v for k, v in c.items() if k != "tools"}
        new["tools"] = {}
        for i, s in enumerate(specs):
            call = calls[f"ta_r1_b{i}"]
            a, _ = _parse(call["raw"])
            if a is None:
                empty += 1
            types = {v.type.value for v in a.vulnerabilities} if a else set()
            new["tools"][s] = {"found": sorted({LLM_TYPE_MAP.get(x, x) for x in types}),
                               "seconds": float(call["seconds"]),
                               "tokens": int(call["prompt_tokens"] + call["completion_tokens"])}
        for t in GENERALISTS + static:
            new["tools"][t] = c["tools"][t]
        for t, rep in qwen.items():
            r = rep[name]
            a, _ = _parse(r["raw"])
            types = {v.type.value for v in a.vulnerabilities} if a else set()
            new["tools"][t] = {"found": sorted({LLM_TYPE_MAP.get(x, x) for x in types}),
                               "seconds": float(r["seconds"]),
                               "tokens": int(r["prompt_tokens"] + r["completion_tokens"])}
        if args.live:
            new["tools"]["slither"] = local[name]
        contracts[name] = new

    out = TR / ("agents_live_qwen_tools.json" if args.live and args.qwen else
                "agents_qwen_tools.json" if args.qwen else
                "agents_live_tools.json" if args.live else
                "agents_hybrid_tools.json" if args.hybrid else "agents_tools.json")
    meta = {k: v for k, v in base.items() if k not in ("contracts", "tools")}
    meta.update({"tools": tools, "built_at": datetime.now().isoformat(timespec="seconds"),
                 "agents_note": "spec_0..5: base TA specialist agents (round-1 replies); "
                                "llm_ba*: generalist agents" + ("; + static tools" if static else "")
                                + ("; llm_qwen_*: Qwen 3.8 27B agents" if args.qwen else "")
                                + ("; slither: run locally (Slither 0.11.6)" if args.live else ""),
                 "n_contracts": len(contracts), "contracts": contracts})
    out.write_text(json.dumps(meta, indent=1), encoding="utf-8")
    print(f"Wrote {len(contracts)} contracts x {len(tools)} agents {tools} -> {out.relative_to(ROOT)}"
          f" ({empty} unparseable specialist replies counted as 'found nothing')")


if __name__ == "__main__":
    main()
