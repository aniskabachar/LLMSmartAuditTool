"""
rl_offline/finish_if_ready.py

Called after each scheduled collection run. Once the v2 collection target is met
(all test contracts + DEFAULT_TRAIN_LIMIT train contracts fully cached), it trains
and evaluates once per label set (curated, all) and writes a marker so it never
repeats for the same data. Does nothing while collection is still incomplete.

Run by hand:  py rl_offline/finish_if_ready.py            (or --force to redo)
"""

from __future__ import annotations
import argparse, json, subprocess, sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from verify import mode_evaluator as ME
from rl_offline.collect import DEFAULT_TRAIN_LIMIT
from rl_offline.common import (CACHE_ROOT, MAX_ROUNDS, cache_path, collection_order, is_complete,
                               list_contracts, load_record, test_label_mix)

MARKER = CACHE_ROOT / "finished.json"


def done(cs) -> int:
    return sum(is_complete(load_record(cache_path(c, ME.MODEL, CACHE_ROOT)), MAX_ROUNDS) for c in cs)


def main():
    from rl_offline.common import utf8_console
    utf8_console()
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    test = list_contracts("test")
    train = collection_order(list_contracts("train"), target=test_label_mix())[:DEFAULT_TRAIN_LIMIT]
    n_test, n_train = done(test), done(train)
    print(f"[finish_if_ready] test {n_test}/{len(test)}, train {n_train}/{len(train)} complete")
    if n_test < len(test) or n_train < len(train):
        print("[finish_if_ready] collection not finished yet; nothing to do.")
        return
    if MARKER.exists() and not args.force:
        print(f"[finish_if_ready] already trained and evaluated ({MARKER}); use --force to redo.")
        return

    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    runs = {}
    for labels in ("curated", "all"):
        tag = f"{stamp}-v2-{labels}"
        for cmd in ([sys.executable, "-u", "rl_offline/train.py", "--tag", tag, "--labels", labels],
                    [sys.executable, "-u", "rl_offline/evaluate.py", "--tag", tag, "--labels", labels]):
            print(f"[finish_if_ready] running: {' '.join(cmd[2:])}", flush=True)
            r = subprocess.run(cmd, cwd=ROOT)
            if r.returncode != 0:
                print(f"[finish_if_ready] FAILED ({r.returncode}); will retry on the next run.")
                return
        runs[labels] = tag
    MARKER.write_text(json.dumps({"finished_at": datetime.now().isoformat(timespec="seconds"),
                                  "tags": runs, "n_test": n_test, "n_train": n_train}, indent=2),
                      encoding="utf-8")
    print(f"[finish_if_ready] done: {runs}. Reports are in verify/results/offline/.")


if __name__ == "__main__":
    main()
