"""
verify/separate_tool_labels.py

Separates curated category labels from static-analysis tool findings in
tasks/*/*.json, and documents every contract whose scoring labels change.

Before: contracts from evaluation/benchmark/Labeled had tool-reported types
appended to `vulnerabilities`, so the grader treated unverified tool output
(including false positives) as ground truth. Tool results were also matched
by file name only, so contracts that share a name across category folders
(e.g. tx_origin/BitCash.sol and TOD/BitCash.sol) received each other's
findings.

After:
  vulnerabilities : the curated category label only (scored by default)
  tool_findings   : tool-reported types matched by folder + file name, with
                    the reporting tools (scored only with labels="all")

SmartBugs Curated contracts (scripts/fetch_smartbugs.py) never had tool
labels and are left untouched.

Writes verify/label_changes.md. Safe to re-run: the "before" state is
recomputed from the tools CSV with the old file-name-only matching.

Run:
    py verify/separate_tool_labels.py            # migrate + regenerate docs
    py verify/separate_tool_labels.py --dry-run  # report only
"""

from __future__ import annotations
import argparse, collections, difflib, hashlib, json, re, sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

from pathlib import Path
from verify.build_ground_truth_from_existing import (
    LABELED_DIR, load_tool_findings, tool_findings_for,
)
from verify.eval_common import ROOT, TIERS, split_path

TASKS_DIR = ROOT / "tasks"
DOC_PATH  = ROOT / "verify" / "label_changes.md"
OLD_TOOL_DESC = "Confirmed by static analysis tools"


def old_basename_index(index) -> dict[str, set[str]]:
    """Reproduce the old lookup: all folders' findings merged per file name."""
    merged: dict[str, set[str]] = collections.defaultdict(set)
    for (_folder, name), types in index.items():
        merged[name].update(types)
    return merged


def split_membership() -> dict[tuple[str, str], str]:
    member = {}
    for tier in TIERS:
        sp = json.loads(split_path(tier).read_text(encoding="utf-8"))
        for part in ("train", "test"):
            for stem in sp[part]:
                member[(tier, stem)] = part
    return member


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def migrate(dry_run: bool) -> tuple[list[dict], list[str], dict]:
    """Rewrite task labels; return (changed rows, warnings, per-tier stats)."""
    index   = load_tool_findings()
    old_idx = old_basename_index(index)
    member  = split_membership()
    rows, warnings = [], []
    stats = {t: collections.Counter() for t in TIERS}

    for tier in TIERS:
        for gt_path in sorted((TASKS_DIR / tier).glob("*.json")):
            d = json.loads(gt_path.read_text(encoding="utf-8"))
            stats[tier]["contracts"] += 1
            if not d.get("source", "").startswith("SmartBugs Labeled"):
                stats[tier]["curated_source"] += 1
                continue
            stats[tier]["labeled_source"] += 1

            curated  = [v for v in d["vulnerabilities"] if v.get("description") != OLD_TOOL_DESC]
            on_disk  = [v["type"] for v in d["vulnerabilities"] if v.get("description") == OLD_TOOL_DESC]
            if len(curated) != 1:
                warnings.append(f"{tier}/{gt_path.name}: expected 1 curated label, found {len(curated)}")
                continue
            curated_type = curated[0]["type"]
            name, category = d["contract"], d["category"]

            # What the grader used to score beyond the curated label
            old_types = sorted(old_idx.get(name, set()) - {curated_type})
            if on_disk and sorted(on_disk) != old_types:
                warnings.append(f"{tier}/{gt_path.name}: on-disk tool labels {sorted(on_disk)} "
                                f"differ from recomputed {old_types}")
                old_types = sorted(on_disk)

            source_sol = LABELED_DIR / category / name
            if not source_sol.exists() or _sha(source_sol) != _sha(gt_path.with_suffix(".sol")):
                warnings.append(f"{tier}/{gt_path.name}: .sol differs from {source_sol.relative_to(ROOT)}"
                                " — tool findings may not describe this exact file")

            new_tools = tool_findings_for(category, name, curated_type, index)
            new_doc = {**{k: v for k, v in d.items() if k not in ("vulnerabilities", "tool_findings")},
                       "vulnerabilities": curated, "tool_findings": new_tools}
            if new_doc != d:
                stats[tier]["files_rewritten"] += 1
                if not dry_run:
                    gt_path.write_text(json.dumps(new_doc, indent=2), encoding="utf-8")

            if old_types:
                folder = category.removeprefix("proc_")
                own = {t: sorted(index.get((folder, name), {}).get(t, set())) for t in old_types}
                elsewhere = {t: sorted(f"{f}/{n}" for (f, n), types in index.items()
                                       if n == name and f != folder and t in types)
                             for t in old_types}
                split = member.get((tier, gt_path.stem), "?")
                stats[tier]["labels_changed"] += 1
                stats[tier][f"labels_changed_{split}"] += 1
                if any(not own[t] for t in old_types):
                    stats[tier]["misattributed"] += 1
                rows.append({
                    "tier": tier, "split": split, "contract": name, "category": category,
                    "curated": curated_type, "removed": old_types,
                    "own_tools": own, "elsewhere": elsewhere,
                })
    return rows, warnings, stats


def _normalise_code(code: str) -> str:
    code = re.sub(r"/\*.*?\*/", "", code, flags=re.S)
    code = re.sub(r"//[^\n]*", "", code)
    return re.sub(r"\s+", " ", code).strip()


def cross_tier_twins() -> list[dict]:
    """Contracts whose file name appears in more than one tier (case-insensitive)."""
    by_stem = collections.defaultdict(list)
    for tier in TIERS:
        for sol in sorted((TASKS_DIR / tier).glob("*.sol")):
            by_stem[sol.stem.lower()].append((tier, sol))
    twins = []
    for stem, items in sorted(by_stem.items()):
        if len(items) < 2:
            continue
        (ta, a), (tb, b) = items[:2]
        la = json.loads(a.with_suffix(".json").read_text(encoding="utf-8"))
        lb = json.loads(b.with_suffix(".json").read_text(encoding="utf-8"))
        sim = difflib.SequenceMatcher(
            None, _normalise_code(a.read_text(encoding="utf-8", errors="replace")),
            _normalise_code(b.read_text(encoding="utf-8", errors="replace"))).ratio()
        twins.append({
            "name": stem, "sim": sim,
            "a": (ta, a.name, la["vulnerabilities"][0]["type"], la["source"]),
            "b": (tb, b.name, lb["vulnerabilities"][0]["type"], lb["source"]),
        })
    return twins


def write_doc(rows: list[dict], warnings: list[str], stats: dict, twins: list[dict]):
    L = []
    L += ["# Ground-truth label changes", "",
          "Generated by `py verify/separate_tool_labels.py`. Do not edit by hand.", "",
          "## What changed", "",
          "- Each task file in `tasks/*/` keeps the dataset's **curated category label** in "
          "`vulnerabilities`. Types reported by static-analysis tools "
          "(`evaluation/LabeledResult/traditional_tools_results.csv`) moved to `tool_findings`, "
          "with the tools that reported them.",
          "- The grader scores `vulnerabilities` only by default (`labels=\"curated\"`). "
          "`labels=\"all\"` also scores `tool_findings` (`--labels all` in "
          "`verify/run_real_evaluation.py` and `verify/regrade.py`, `?labels=all` in the API).",
          "- Tool findings are now matched by category folder **and** file name. Before, "
          "matching by file name alone merged findings from different contracts that share a "
          "name (marked *other file* below).",
          "- Only contracts from `evaluation/benchmark/Labeled` were affected; SmartBugs Curated "
          "contracts never had tool labels.", "",
          "## Summary", "",
          "| Tier | Contracts | From Labeled set | Scoring labels changed | of which in test split | With misattributed tool labels |",
          "|---|---:|---:|---:|---:|---:|"]
    tot = collections.Counter()
    for tier in TIERS:
        s = stats[tier]
        tot.update(s)
        L.append(f"| {tier} | {s['contracts']} | {s['labeled_source']} | {s['labels_changed']} "
                 f"| {s['labels_changed_test']} | {s['misattributed']} |")
    L.append(f"| **total** | {tot['contracts']} | {tot['labeled_source']} | {tot['labels_changed']} "
             f"| {tot['labels_changed_test']} | {tot['misattributed']} |")
    L += ["", f"After this change every contract has exactly one scoring label "
              f"(before: {tot['labels_changed']} contracts had 2–4).", "",
          "## Contracts whose scoring labels changed", "",
          "*Removed from scoring* lists the tool-reported types the grader used to count as "
          "ground truth. Each is followed by the tools that reported it for this file, or by "
          "*other file* when only a different, same-named contract had that finding.", "",
          "| Tier | Split | Contract | Category | Curated label (kept) | Removed from scoring |",
          "|---|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda r: (TIERS.index(r["tier"]), r["split"] != "test", r["contract"].lower())):
        parts = []
        for t in r["removed"]:
            if r["own_tools"][t]:
                parts.append(f"`{t}` ({', '.join(r['own_tools'][t])})")
            else:
                parts.append(f"`{t}` (*other file*: {', '.join(r['elsewhere'][t])})")
        split = f"**{r['split']}**" if r["split"] == "test" else r["split"]
        L.append(f"| {r['tier']} | {split} | {r['contract']} | {r['category']} "
                 f"| `{r['curated']}` | {'; '.join(parts)} |")

    L += ["", "## Known issue, not changed: same contract in two tiers", "",
          "These file names occur in two tiers. Where code similarity is high they are "
          "effectively the same contract, sometimes with a **different curated label** from the "
          "two source datasets (SmartBugs Curated vs the Labeled benchmark). Grading either copy "
          "counts the other copy's label as a false positive. Low-similarity pairs are different "
          "contracts that share a name.", "",
          "| Name | Tier / label | Tier / label | Code similarity | Same label |",
          "|---|---|---|---:|---|"]
    for t in twins:
        a, b = t["a"], t["b"]
        L.append(f"| {t['name']} | {a[0]} / `{a[2]}` | {b[0]} / `{b[2]}` | {t['sim']:.2f} "
                 f"| {'yes' if a[2] == b[2] else '**no**'} |")

    L += ["", "## Interpreting scores", "",
          "- Curated labels are single-category. A contract can contain real bugs of other "
          "types; reporting them counts as a false positive under `labels=\"curated\"`, so "
          "precision is a lower bound.",
          "- `labels=\"all\"` is not a better ground truth: tool findings are unverified and "
          "include false positives. Use it only as a sensitivity comparison."]
    if warnings:
        L += ["", "## Warnings from the last run", ""] + [f"- {w}" for w in warnings]
    DOC_PATH.write_text("\n".join(L) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Report changes without rewriting task files or docs")
    args = parser.parse_args()

    rows, warnings, stats = migrate(args.dry_run)
    for tier in TIERS:
        s = stats[tier]
        print(f"  {tier:<8} contracts={s['contracts']:<3} labeled={s['labeled_source']:<3} "
              f"labels_changed={s['labels_changed']:<3} (test={s['labels_changed_test']})  "
              f"misattributed={s['misattributed']}  files_rewritten={s['files_rewritten']}")
    for w in warnings:
        print(f"  [WARN] {w}")
    if args.dry_run:
        print("  Dry run: no files written.")
        return
    write_doc(rows, warnings, stats, cross_tier_twins())
    print(f"  Wrote {DOC_PATH.relative_to(ROOT).as_posix()}")


if __name__ == "__main__":
    main()
