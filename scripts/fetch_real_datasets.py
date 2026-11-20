"""
scripts/fetch_real_datasets.py

Adds real, labelled Solidity contracts to a TRAINING-ONLY pool in
datasets/real/<tier>/, so the RL policies can learn from real LLM audit
outcomes on more contracts. tasks/ and the held-out test split are not touched.

Sources (both public, MIT/Apache licensed, cloned from GitHub):

  smartbugs-curated   https://github.com/smartbugs/smartbugs-curated
      143 contracts with line-level labels (vulnerabilities.json). tasks/ already
      holds part of it; only contracts not already in tasks/ are added.
  SolidiFI-benchmark  https://github.com/smartbugs/SolidiFI-benchmark
      350 real ERC-20 style contracts, 50 per bug class, each with bugs injected
      at known lines (BugLog_<n>.csv). Ghaleb & Pattabiraman, ISSTA 2020.

Leakage guard: every candidate is compared with every contract in tasks/
(train AND test) on normalised source (comments and whitespace stripped).
  - exact duplicate of any tasks/ contract                -> dropped
  - Jaccard similarity >= --test-sim with a TEST contract  -> dropped
  - Jaccard similarity >= --dup-sim with a TRAIN contract  -> dropped (already in pool)
So no test contract, nor a lightly edited copy of one, reaches training.

Truncation guard: the audit modes send at most MAX_CODE_CHARS characters of code
(head + tail). A candidate is kept only if at least one labelled bug line
survives that truncation, so the label stays observable to the LLM.

Run:
    py scripts/fetch_real_datasets.py                    # clone + build
    py scripts/fetch_real_datasets.py --src-dir D:/clones  # use existing clones
Writes datasets/real/<tier>/*.sol|.json and datasets/real/manifest.json.
"""

from __future__ import annotations
import argparse, csv, hashlib, json, re, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT_DIR = ROOT / "datasets" / "real"
REPOS = {
    "smartbugs-curated":  "https://github.com/smartbugs/smartbugs-curated.git",
    "SolidiFI-benchmark": "https://github.com/smartbugs/SolidiFI-benchmark.git",
}
MAX_CODE_CHARS = 6000   # must match verify/mode_evaluator.py

# Curated category -> (tier, grader type, severity); same mapping as scripts/fetch_smartbugs.py
CURATED_MAP = {
    "reentrancy":                ("easy",   "reentrancy",                "high"),
    "arithmetic":                ("easy",   "integer_overflow",          "medium"),
    "access_control":            ("medium", "access_control",            "high"),
    "unchecked_low_level_calls": ("medium", "unchecked_low_level_calls", "medium"),
    "time_manipulation":         ("medium", "timestamp_dependence",      "medium"),
    "bad_randomness":            ("hard",   "bad_randomness",            "high"),
    "front_running":             ("hard",   "front_running",             "medium"),
    "denial_of_service":         ("hard",   "denial_of_service",         "medium"),
    "short_addresses":           ("hard",   "other",                     "low"),
    "other":                     ("hard",   "other",                     "low"),
}

# SolidiFI bug class -> (tier, grader type, severity); tiers follow the matching
# SmartBugs Labeled categories already in tasks/ (proc_reentrancy, proc_TOD, ...)
SOLIDIFI_MAP = {
    "Re-entrancy":          ("easy",   "reentrancy",                "high"),
    "Overflow-Underflow":   ("easy",   "integer_overflow",          "medium"),
    "tx.origin":            ("easy",   "tx_origin",                 "high"),
    "TOD":                  ("medium", "front_running",             "medium"),
    "Timestamp-Dependency": ("medium", "timestamp_dependence",      "medium"),
    "Unchecked-Send":       ("medium", "unchecked_low_level_calls", "medium"),
    "Unhandled-Exceptions": ("medium", "unchecked_low_level_calls", "medium"),
}

# ── Normalisation and similarity ──────────────────────────────────────────────

_COMMENTS = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
_TOKENS   = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+|[^\sA-Za-z0-9_]")


def normalise(code: str) -> str:
    return re.sub(r"\s+", " ", _COMMENTS.sub(" ", code)).strip()


def shingles(code: str, k: int = 5) -> set[int]:
    toks = _TOKENS.findall(normalise(code))
    return {hash(tuple(toks[i:i + k])) for i in range(max(1, len(toks) - k + 1))}


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def code_hash(code: str) -> str:
    return hashlib.sha256(normalise(code).encode("utf-8")).hexdigest()


# ── Truncation check ──────────────────────────────────────────────────────────

def visible_lines(code: str, max_chars: int = MAX_CODE_CHARS) -> set[int]:
    """1-based line numbers whose text survives verify/real_evaluator.truncate()."""
    if len(code) <= max_chars:
        return set(range(1, code.count("\n") + 2))
    half = max_chars // 2
    head_end, tail_start = half, len(code) - half
    vis, pos = set(), 0
    for i, line in enumerate(code.split("\n"), start=1):
        start, end = pos, pos + len(line)
        if end <= head_end or start >= tail_start:
            vis.add(i)
        pos = end + 1
    return vis


# ── Sources ───────────────────────────────────────────────────────────────────

def clone(src_dir: Path):
    src_dir.mkdir(parents=True, exist_ok=True)
    for name, url in REPOS.items():
        dest = src_dir / name
        if dest.exists():
            continue
        print(f"Cloning {url}")
        subprocess.run(["git", "clone", "-q", "--depth", "1", url, str(dest)], check=True)


def repo_commit(path: Path) -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=path, capture_output=True,
                              text=True, timeout=10).stdout.strip() or None
    except Exception:
        return None


def curated_candidates(src: Path):
    meta = json.loads((src / "vulnerabilities.json").read_text(encoding="utf-8"))
    for entry in meta:
        cat = entry["path"].split("/")[1]
        if cat not in CURATED_MAP:
            continue
        tier, vtype, sev = CURATED_MAP[cat]
        lines = sorted({l for v in entry["vulnerabilities"] for l in v["lines"]})
        yield {
            "id":       f"sbc__{Path(entry['name']).stem}",
            "code":     (src / entry["path"]).read_text(encoding="utf-8", errors="replace"),
            "tier":     tier,
            "gt": {
                "contract": entry["name"],
                "source":   "SmartBugs Curated (extra, training pool)",
                "category": cat,
                "vulnerabilities": [{
                    "type": vtype, "severity": sev,
                    "location": "lines " + ",".join(map(str, lines)),
                    "description": f"Known {vtype} vulnerability from SmartBugs Curated "
                                   f"({entry['path']})"}],
                "lines":    lines,
                "origin":   f"smartbugs-curated/{entry['path']}",
            },
        }


def solidifi_candidates(src: Path):
    base = src / "buggy_contracts"
    for cls, (tier, vtype, sev) in SOLIDIFI_MAP.items():
        for sol in sorted((base / cls).glob("buggy_*.sol"), key=lambda p: int(p.stem.split("_")[1])):
            n = sol.stem.split("_")[1]
            log = base / cls / f"BugLog_{n}.csv"
            lines = []
            if log.exists():
                with log.open(encoding="utf-8", errors="replace") as fh:
                    for row in csv.DictReader(fh):
                        try:
                            start, length = int(row["loc"]), int(row["length"])
                        except (KeyError, ValueError):
                            continue
                        lines.extend(range(start, start + max(1, length)))
            slug = re.sub(r"[^A-Za-z0-9]", "", cls).lower()
            yield {
                "id":   f"solidifi__{slug}_{n}",
                "code": sol.read_text(encoding="utf-8", errors="replace"),
                "tier": tier,
                "gt": {
                    "contract": f"{cls}/{sol.name}",
                    "source":   "SolidiFI benchmark (training pool)",
                    "category": f"solidifi_{slug}",
                    "vulnerabilities": [{
                        "type": vtype, "severity": sev,
                        "location": f"{len(set(lines))} injected lines",
                        "description": f"{cls} bugs injected by SolidiFI at known lines"}],
                    "lines":  sorted(set(lines)),
                    "origin": f"SolidiFI-benchmark/buggy_contracts/{cls}/{sol.name}",
                },
            }


# ── Build ─────────────────────────────────────────────────────────────────────

def load_tasks() -> dict[str, list[dict]]:
    """tasks/ contracts grouped by split part, with shingles for similarity."""
    from verify.eval_common import TIERS, contracts_for
    parts = {"train": [], "test": []}
    for tier in TIERS:
        for part in parts:
            for p in contracts_for(tier, part):
                code = p.read_text(encoding="utf-8", errors="replace")
                gt = json.loads(p.with_suffix(".json").read_text(encoding="utf-8"))
                parts[part].append({"name": f"{tier}/{p.name}", "hash": code_hash(code),
                                    "sh": shingles(code),
                                    "types": {v["type"] for v in gt["vulnerabilities"]}})
    return parts


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--src-dir", default=str(ROOT / "datasets" / "_sources"),
                    help="Where the source repos are (cloned here if missing)")
    ap.add_argument("--out-dir", default=str(OUT_DIR))
    ap.add_argument("--test-sim", type=float, default=0.5,
                    help="Drop candidates at least this similar to any TEST contract")
    ap.add_argument("--dup-sim", type=float, default=0.9,
                    help="Drop candidates at least this similar to a same-label train or kept contract")
    args = ap.parse_args()

    src_dir, out_dir = Path(args.src_dir), Path(args.out_dir)
    clone(src_dir)
    tasks = load_tasks()
    all_hashes = {t["hash"] for part in tasks.values() for t in part}

    candidates = (list(curated_candidates(src_dir / "smartbugs-curated"))
                  + list(solidifi_candidates(src_dir / "SolidiFI-benchmark")))
    print(f"{len(candidates)} candidates; tasks/: {len(tasks['train'])} train, "
          f"{len(tasks['test'])} test")

    kept, dropped = [], {"exact_dup_of_tasks": [], "near_test": [], "near_train_or_kept": [],
                         "bug_truncated": []}
    kept_sh: list[tuple[str, set]] = []   # (type, shingles)
    for c in candidates:
        h = code_hash(c["code"])
        if h in all_hashes:
            dropped["exact_dup_of_tasks"].append(c["id"]); continue
        sh = shingles(c["code"])
        near_test = max(((jaccard(sh, t["sh"]), t["name"]) for t in tasks["test"]), default=(0, ""))
        if near_test[0] >= args.test_sim:
            dropped["near_test"].append({"id": c["id"], "similar_to": near_test[1],
                                         "jaccard": round(near_test[0], 3)}); continue
        # Near-copies only count as duplicates when they carry the same label: SolidiFI
        # reuses host contracts with different injected bugs, which is useful signal.
        ctype = c["gt"]["vulnerabilities"][0]["type"]
        near_train = max([jaccard(sh, t["sh"]) for t in tasks["train"] if ctype in t["types"]]
                         + [jaccard(sh, k) for kt, k in kept_sh if kt == ctype], default=0)
        if near_train >= args.dup_sim:
            dropped["near_train_or_kept"].append(c["id"]); continue
        lines = set(c["gt"]["lines"])
        if lines and not (lines & visible_lines(c["code"])):
            dropped["bug_truncated"].append(c["id"]); continue
        c["gt"]["max_jaccard_vs_test"] = round(near_test[0], 3)
        c["gt"]["code_sha256"] = h
        kept.append(c)
        kept_sh.append((ctype, sh))

    for tier in ("easy", "medium", "hard"):
        (out_dir / tier).mkdir(parents=True, exist_ok=True)
        for old in (out_dir / tier).glob("*"):
            old.unlink()
    for c in kept:
        (out_dir / c["tier"] / f"{c['id']}.sol").write_text(c["code"], encoding="utf-8")
        (out_dir / c["tier"] / f"{c['id']}.json").write_text(json.dumps(c["gt"], indent=2),
                                                             encoding="utf-8")

    by = lambda key: dict(sorted(__import__("collections").Counter(key(c) for c in kept).items()))
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "purpose": "training pool only; never evaluated as test data",
        "sources": {n: {"url": u, "commit": repo_commit(src_dir / n)} for n, u in REPOS.items()},
        "thresholds": {"test_jaccard": args.test_sim, "dup_jaccard": args.dup_sim,
                       "max_code_chars": MAX_CODE_CHARS},
        "kept": len(kept),
        "kept_by_tier": by(lambda c: c["tier"]),
        "kept_by_source": by(lambda c: c["id"].split("__")[0]),
        "kept_by_type": by(lambda c: c["gt"]["vulnerabilities"][0]["type"]),
        "dropped_counts": {k: len(v) for k, v in dropped.items()},
        "dropped": dropped,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({k: manifest[k] for k in
                      ("kept", "kept_by_tier", "kept_by_source", "kept_by_type", "dropped_counts")},
                     indent=2))


if __name__ == "__main__":
    main()
