"""
rl_tools/build_paper_dataset.py

The base paper's own evaluation data (LLM-SmartAudit, evaluation/benchmark/), so our
coordinator can be compared with the base TA/BA modes on exactly the paper's contracts:

  Labelled dataset   evaluation/benchmark/Labeled/proc_*  (the set behind the paper's
                     accuracy tables, evaluation/LabeledResult/: ~10 contracts per type)
                       102 vulnerable contracts, 10 bug types   origin "paper_labeled"
                        10 safe contracts (proc_safecontract)   origin "paper_safe"
  CVE dataset        evaluation/benchmark/cve: 13 contracts     origin "paper_cve"

Not included: Labeled/proc_code3rena (96 Code4rena files) and Realworld/ (102 projects,
6,454 files): their bugs are listed per contest (Web3Bugs), not per file, and the paper
checked them by hand; they cannot be scored automatically per contract.

Labels: the folder's bug type (the paper's 10 types) mapped to our categories exactly as
tasks/*.json and the LLM findings are mapped (tx.origin, selfdestruct, delegatecall ->
access_control; gasless send -> unchecked_low_level_calls, as in tasks/). CVE labels from
their CVE.org descriptions (CVE_LABELS below). Safe contracts have no label and are scored
separately (false alarms), since 0.6*recall + 0.4*precision is undefined without a bug.

--init      writes datasets/tool_results/paper_tools.json (contracts, labels, features,
            "in_training": same code as one of the 269 coordinator training contracts) and
            copies replies already collected for contracts whose prompt text is identical
            (same stripped code -> same LLM request), so they are not paid for twice.
--assemble  after collection, writes datasets/tool_results/paper_agents_tools.json with the
            13 analysers of the deployed coordinator for every contract.

Run:  py rl_tools/build_paper_dataset.py --init
      (collect: run_paper_collection.ps1)
      py rl_tools/build_paper_dataset.py --assemble
"""

from __future__ import annotations
import argparse, json, sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TR = ROOT / "datasets" / "tool_results"
BENCH = ROOT / "evaluation" / "benchmark"
OUT = TR / "paper_tools.json"
OUT_AGENTS = TR / "paper_agents_tools.json"

FOLDER_LABEL = {                      # the paper's 10 types -> our categories (as tasks/*.json)
    "proc_reentrancy": "reentrancy", "proc_arithmetic": "integer_overflow",
    "proc_unchecked_send": "unchecked_low_level_calls", "proc_gasless_send": "unchecked_low_level_calls",
    "proc_unsafe_delegatecall": "access_control", "proc_time_manipulation": "timestamp_dependence",
    "proc_TOD": "front_running", "proc_bad_randomness": "bad_randomness",
    "proc_tx_origin": "access_control", "proc_unsafe_suicide": "access_control",
    "proc_safecontract": None,
}
CVE_LABELS = {                        # from the CVE.org description of each entry
    "CVE-2018-14085": "access_control",     # UserWallet: sweeper delegatecall overwrites owner
    "CVE-2018-14087": "integer_overflow",   # EUC: msg.value * buyPrice overflows
    "CVE-2018-14089": "other",              # Virgo_ZodiacToken: '<=' instead of '>=' in transferFrom
    "CVE-2018-17111": "access_control",     # Coinlancer: broken onlyOwner comparison
    "CVE-2018-17987": "bad_randomness",     # HashHeroes Tiles: blockhash as random number
    "CVE-2018-18425": "other",              # Primeo: airdrop ignores the supply cap
    "CVE-2019-15079": "access_control",     # EAI: constructor name typo -> public function
    "CVE-2021-3004": "other",               # yCREDIT: incorrect _deposit calculation
    "CVE-2021-33403": "integer_overflow",   # Lancer Token: overflow in transfer
    "CVE-2021-34270": "integer_overflow",   # Doftcoin: overflow in mintToken
    "CVE-2021-34272": "access_control",     # RobotCoin: public 'owned' function -> owner hijack
    "CVE-2021-34273": "access_control",     # BTC2X: public 'owned' function -> owner hijack
    "CVE-2023-26488": "other",              # OZ ERC721Consecutive: balance not updated (batch of 1)
}
# reply files of the 13 analysers (+ the base TA mode); slither is run locally on every contract
REPLY_FILES = ["llm_ba", "llm_ba_med", "llm_ba_dasp", "llm_qwen_ba", "llm_qwen_think", "llm_qwen_dasp", "llm_ta"]


def prompt_text(path: Path) -> str:
    from verify import mode_evaluator as ME
    from verify.real_evaluator import strip_comments, truncate
    return truncate(strip_comments(path.read_text(encoding="utf-8", errors="replace")), ME.MAX_CODE_CHARS)


def init():
    import hashlib, re
    from rl_offline.common import _features
    from verify.real_evaluator import strip_comments
    norm = lambda p: hashlib.md5(re.sub(r"\s+", "", strip_comments(
        p.read_text(encoding="utf-8", errors="replace"))).encode()).hexdigest()
    train = json.loads((TR / "combined_tools.json").read_text(encoding="utf-8"))["contracts"]
    real = json.loads((TR / "real_tools.json").read_text(encoding="utf-8"))["contracts"]
    train_h = {norm(ROOT / c["source"]) for c in train.values()}
    existing = {}                                       # identical prompt text -> existing name
    for n, c in {**real, **train}.items():
        existing.setdefault(prompt_text(ROOT / c["source"]), n)

    files = [(d.name, p) for d in sorted((BENCH / "Labeled").glob("proc_*")) if d.name in FOLDER_LABEL
             for p in sorted(d.glob("*.sol"))] + [("cve", p) for p in sorted((BENCH / "cve").glob("*.sol"))]
    contracts, same = {}, {}
    for folder, p in files:
        name = f"paper__{folder.removeprefix('proc_')}__{p.stem}"
        if folder == "cve":
            labels, origin = [CVE_LABELS[p.stem]], "paper_cve"
        elif FOLDER_LABEL[folder] is None:
            labels, origin = [], "paper_safe"
        else:
            labels, origin = [FOLDER_LABEL[folder]], "paper_labeled"
        contracts[name] = {"labels": labels, "source": p.relative_to(ROOT).as_posix(), "origin": origin,
                           "paper_type": folder.removeprefix("proc_"),
                           "in_training": norm(p) in train_h,
                           "features": [float(x) for x in _features(p)]}
        if (t := prompt_text(p)) in existing:
            same[name] = existing[t]

    from collections import Counter
    OUT.write_text(json.dumps({
        "source": "LLM-SmartAudit evaluation/benchmark: Labeled/proc_* (except proc_code3rena) + cve",
        "built_at": datetime.now().isoformat(timespec="seconds"),
        "n_contracts": len(contracts), "origins": dict(Counter(c["origin"] for c in contracts.values())),
        "n_in_training": sum(c["in_training"] for c in contracts.values()),
        "contracts": contracts}, indent=1), encoding="utf-8")
    print(f"Wrote {len(contracts)} contracts {dict(Counter(c['origin'] for c in contracts.values()))}; "
          f"{sum(c['in_training'] for c in contracts.values())} share code with the 269 training contracts "
          f"-> {OUT.relative_to(ROOT)}")

    # reuse replies collected earlier for an identical request
    for f in REPLY_FILES:
        path = TR / f"{f}.json"
        rep = json.loads(path.read_text(encoding="utf-8"))
        n = 0
        for new, old in same.items():
            if new not in rep["contracts"] and old in rep["contracts"]:
                rec = rep["contracts"][old]
                if f == "llm_ta" and not all(k in rec.get("calls", {}) for k in rep["keys"]):
                    continue
                rep["contracts"][new] = {**rec, "copied_from": old}
                n += 1
        if n:
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(rep, indent=1), encoding="utf-8")
            tmp.replace(path)
        have = sum(1 for c in contracts if c in rep["contracts"])
        print(f"  {f}: reused {n} identical-request replies; {have}/{len(contracts)} covered")


def assemble():
    from rl_offline.common import _parse, utf8_console
    from rl_tools.build_dataset import CATEGORY_MAP, LLM_TYPE_MAP, load_mapping
    utf8_console()
    base = json.loads(OUT.read_text(encoding="utf-8"))
    rep = {f: json.loads((TR / f"{f}.json").read_text(encoding="utf-8"))["contracts"] for f in REPLY_FILES}
    mapping = load_mapping(ROOT / "datasets" / "smartbugs_meta")
    sl = json.loads((TR / "slither_local.json").read_text(encoding="utf-8"))["contracts"]
    names = list(base["contracts"])
    gaps = {f: [n for n in names if n not in r or (f == "llm_ta" and len(r[n].get("calls", {})) < 8)]
            for f, r in rep.items()}
    gaps["slither"] = [n for n in names if n not in sl]
    if any(gaps.values()):
        raise SystemExit("missing replies: " + ", ".join(f"{f} {len(g)}" for f, g in gaps.items() if g))

    def found_of(raw):
        a, _ = _parse(raw)
        return sorted({LLM_TYPE_MAP.get(v.type.value, v.type.value) for v in a.vulnerabilities}) if a else []

    out = {}
    for n in names:
        c = dict(base["contracts"][n])
        tools = {}
        calls = rep["llm_ta"][n]["calls"]
        for i in range(6):
            k = calls[f"ta_r1_b{i}"]
            tools[f"spec_{i}"] = {"found": found_of(k["raw"]), "seconds": float(k["seconds"]),
                                  "tokens": int(k["prompt_tokens"] + k["completion_tokens"])}
        for f in REPLY_FILES[:-1]:
            r = rep[f][n]
            tools[f] = {"found": found_of(r["raw"]), "seconds": float(r["seconds"]),
                        "tokens": int(r["prompt_tokens"] + r["completion_tokens"])}
        r = sl[n]
        cats = {mapping.get(("slither", chk.strip().lower())) for chk in r["checks"]}
        tools["slither"] = {"found": sorted({CATEGORY_MAP[x] for x in cats if x in CATEGORY_MAP}),
                            "seconds": float(r["seconds"]), "compiled": bool(r["ok"])}
        c["tools"] = tools
        out[n] = c
    tools = [f"spec_{i}" for i in range(6)] + REPLY_FILES[:-1] + ["slither"]
    OUT_AGENTS.write_text(json.dumps({**{k: v for k, v in base.items() if k != "contracts"},
                                      "tools": tools, "assembled_at": datetime.now().isoformat(timespec="seconds"),
                                      "contracts": out}, indent=1), encoding="utf-8")
    print(f"Wrote {len(out)} contracts x {len(tools)} analysers -> {OUT_AGENTS.relative_to(ROOT)}")


if __name__ == "__main__":
    from rl_offline.common import utf8_console
    utf8_console()
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--init", action="store_true")
    g.add_argument("--assemble", action="store_true")
    a = ap.parse_args()
    init() if a.init else assemble()
