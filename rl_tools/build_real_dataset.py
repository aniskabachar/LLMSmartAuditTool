"""
rl_tools/build_real_dataset.py

Real-bugs-only tool dataset: all labelled SmartBugs curated contracts in tasks/ (179),
with tools that can be run on every one of them:
  slither      run locally (rl_tools/run_slither.py), real measured run time
  llm_ba, llm_ba_med, llm_ba_dasp   one LLM call each (rl_tools/collect_llm.py --data <this file>)

Labels: the curated label of each contract (tasks/*.json), mapped like LLM findings
(tx_origin and selfdestruct -> access_control, as in the SmartBugs/DASP taxonomy).
Slither findings: SmartBugs mapping CSV + aliases (rl_tools/build_dataset.py).
Contracts Slither could not compile count as "ran, found nothing" (what a user would get).
LLM tools are included only once they cover every contract.

Run:  py rl_tools/build_real_dataset.py --src <smartbugs-results clone>
Out:  datasets/tool_results/real_tools.json
"""

from __future__ import annotations
import argparse, json, sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT = ROOT / "datasets" / "tool_results" / "real_tools.json"
SLITHER = ROOT / "datasets" / "tool_results" / "slither_local.json"


def main():
    from rl_offline.common import list_contracts, _features, _parse, utf8_console
    from rl_tools.build_dataset import CATEGORY_MAP, LLM_TOOLS, LLM_TYPE_MAP, load_mapping
    utf8_console()
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="Local clone of smartbugs-results (for the mapping CSV)")
    args = ap.parse_args()
    mapping = load_mapping(Path(args.src))
    sl = json.loads(SLITHER.read_text(encoding="utf-8"))

    real = {}
    for part in ("test", "train"):
        for c in list_contracts(part):
            if c.origin == "tasks":
                real.setdefault(c.stem, c)
    missing = [n for n in real if n not in sl["contracts"]]
    if missing:
        raise SystemExit(f"Slither results missing for {len(missing)} contracts; run rl_tools/run_slither.py")

    # SolidiFI contracts hidden in tasks/ (injected bugs, recognisable by SolidiFI's names):
    # not real-world bugs, so excluded from this dataset.
    import re
    solidifi_like = re.compile(r"_(intou|re_ent|tmstmp|unchk|txorigin|tod)\d+\b")
    excluded = sorted(n for n, c in real.items() if solidifi_like.search(c.code()))
    if excluded:
        print(f"excluded {len(excluded)} SolidiFI-injected contracts found in tasks/: {excluded}")
    unmapped, contracts = {}, {}
    for name in sorted(real):
        if name in excluded:
            continue
        c = real[name]
        gt = c.ground_truth()
        # "SmartBugs Curated" = real-world contracts; "SmartBugs Labeled / proc_*" = the labelled
        # benchmark used by the base paper (its 10 vulnerability types)
        origin = "smartbugs_curated" if gt.get("source", "").startswith("SmartBugs Curated") else "paper_labeled"
        labels = sorted({LLM_TYPE_MAP.get(v["type"], v["type"]) for v in gt["vulnerabilities"]})
        r = sl["contracts"][name]
        found = set()
        for chk in r["checks"]:
            cat = mapping.get(("slither", chk.strip().lower()))
            if cat is None:
                unmapped[chk] = unmapped.get(chk, 0) + 1
            elif cat in CATEGORY_MAP:
                found.add(CATEGORY_MAP[cat])
        contracts[name] = {"labels": labels, "source": c.sol.relative_to(ROOT).as_posix(),
                           "origin": origin, "split_in_repo": c.part, "tier": c.tier,
                           "features": [float(x) for x in _features(c.sol)],
                           "tools": {"slither": {"found": sorted(found), "seconds": float(r["seconds"]),
                                                 "compiled": bool(r["ok"])}}}
    if unmapped:
        print("unmapped Slither checks (ignored):", dict(sorted(unmapped.items(), key=lambda x: -x[1])))

    tool_names = ["slither"]
    for t, p in LLM_TOOLS.items():
        llm = json.loads(p.read_text(encoding="utf-8"))["contracts"] if p.exists() else {}
        gap = [n for n in contracts if n not in llm]
        if gap:
            print(f"{t}: no reply for {len(gap)} contracts -> left out; run "
                  f"py rl_tools/collect_llm.py --data {OUT.relative_to(ROOT)} --variant {t.removeprefix('llm_')}")
            continue
        for n, c in contracts.items():
            a, _ = _parse(llm[n]["raw"])
            types = {v.type.value for v in a.vulnerabilities} if a else set()
            c["tools"][t] = {"found": sorted({LLM_TYPE_MAP.get(x, x) for x in types}),
                             "seconds": float(llm[n]["seconds"]),
                             "tokens": int(llm[n]["prompt_tokens"] + llm[n]["completion_tokens"])}
        tool_names.append(t)

    OUT.write_text(json.dumps({
        "source": "SmartBugs curated contracts in tasks/ (labels); Slither run locally; LLM via Groq",
        "source_commit": sl.get("slither"),
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tools": tool_names, "n_contracts": len(contracts), "excluded_solidifi_like": excluded,
        "slither_compiled": sum(c["tools"]["slither"]["compiled"] for c in contracts.values()),
        "contracts": contracts}, indent=1), encoding="utf-8")
    print(f"Wrote {len(contracts)} real contracts x {len(tool_names)} tools -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
