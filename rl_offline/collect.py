"""
rl_offline/collect.py

Collects the real LLM audit cache that the offline RL environments replay.

For every contract: BA round 1, TA round 1 (6 detector batches), and review
rounds 2..MAX_ROUNDS for BA, TA and HYBRID — exactly the prompts, model and
settings of verify/mode_evaluator.py. Each call is written to the contract's
cache file as soon as it returns, so the run can be stopped (Ctrl+C, daily
limit, network) and resumed with the same command; nothing is redone.

Failed calls are never stored and never scored as 0: the contract just stays
incomplete until a later run fills the gap.

Order: the 40 held-out test contracts first, then the training pool
(tasks/ train split + datasets/real) in a seeded, label-balanced order, so any
partial collection is usable.

Run (on the machine with GROQ_API_KEY in .env):
    py rl_offline/collect.py --status                    # what is cached, token estimate
    py rl_offline/collect.py                             # test split, then train pool
    py rl_offline/collect.py --part train --limit 150    # stop after 150 train contracts
    py rl_offline/collect.py --tpm 8000 --rpm 30         # your Groq limits (console -> Limits)
    py rl_offline/collect.py --workers 8                 # 8 contracts at once, one key each
    py rl_offline/collect.py --backend mock --cache-root datasets/cache_mock   # pipeline test
"""

from __future__ import annotations
import argparse, sys, time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from verify import mode_evaluator as ME
from verify.real_evaluator import truncate
from rl_offline.common import (
    CACHE_ROOT, MAX_ROUNDS, MODES, Contract, cache_path, collection_order, is_complete,
    list_contracts, load_record, required_keys, test_label_mix, review_input_action, save_record,
)
from rl_offline.llm import AccessDenied, CallFailed, DailyLimitReached, GroqBackend, KeyPool, MockBackend, load_keys

PROMPT_HASH = ME.config("ba")["prompt_hash"]
DEFAULT_TRAIN_LIMIT = 60      # train contracts in the v2 collection target (test-matched order)


def new_record(c: Contract, backend) -> dict:
    return {
        "contract": c.stem, "origin": c.origin, "tier": c.tier, "part": c.part,
        "source_path": c.sol.relative_to(ROOT).as_posix(),
        "backend": backend.name, "model": ME.MODEL, "temperature": ME.TEMPERATURE,
        "max_tokens": ME.MAX_TOKENS, "reasoning_effort": ME.REASONING_EFFORT,
        "comments_stripped": True,
        "max_code_chars": ME.MAX_CODE_CHARS,
        "prompt_hash": PROMPT_HASH, "n_ta_batches": len(ME.TA_BATCH_TEXTS),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "calls": {},
    }


def prompt_for(key: str, code: str, rec: dict) -> tuple[str, str]:
    """System and user prompt for one call key (identical to mode_evaluator's)."""
    if key == "ba_r1":
        return ME.BA_SYSTEM, f"Contract to audit:\n\n{code}"
    if key.startswith("ta_r1_b"):
        batch = ME.TA_BATCH_TEXTS[int(key.rsplit("b", 1)[1])]
        return ME.TA_SYSTEM, (f"Vulnerability scenarios to check:\n\n{batch}"
                              f"\n\nContract to audit:\n\n{code}")
    mode, k = key.split("_r")
    findings = review_input_action(rec, mode.upper(), int(k))
    return ME.REVIEW_SYSTEM, (f"Contract:\n\n{code}\n\nFindings from the previous round:\n"
                              f"{ME._findings_json(findings)}\n\nReturn the reviewed list.")


def collect_contract(c: Contract, backend, cache_root: Path, max_rounds: int) -> tuple[int, int]:
    """Fill every missing call of one contract. Returns (new calls, new tokens)."""
    path = cache_path(c, backend.model, cache_root)
    rec = load_record(path) or new_record(c, backend)
    if rec.get("prompt_hash") != PROMPT_HASH:
        raise SystemExit(f"{path} was collected with different prompts "
                         f"({rec.get('prompt_hash')} != {PROMPT_HASH}). Use a new --cache-root.")
    if (rec.get("max_tokens"), rec.get("reasoning_effort"), rec.get("comments_stripped")) != (
            ME.MAX_TOKENS, ME.REASONING_EFFORT, True):
        raise SystemExit(f"{path} was collected with max_tokens={rec.get('max_tokens')}, "
                         f"reasoning_effort={rec.get('reasoning_effort')}, "
                         f"comments_stripped={rec.get('comments_stripped')}; use a new --cache-root.")
    if rec.get("backend", "groq") != backend.name:
        raise SystemExit(f"{path} holds {rec.get('backend')} data; use a separate --cache-root.")
    code = truncate(c.code(), ME.MAX_CODE_CHARS)
    n_calls = n_tok = 0
    for key in required_keys(rec["n_ta_batches"], max_rounds):   # review keys come after r1
        if key in rec["calls"]:
            continue
        system, user = prompt_for(key, code, rec)
        out = backend.call(system, user)
        out["at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        rec["calls"][key] = out
        save_record(path, rec)
        n_calls += 1
        n_tok += out["prompt_tokens"] + out["completion_tokens"]
    return n_calls, n_tok


def status(model: str, cache_root: Path, max_rounds: int, limit: int | None = DEFAULT_TRAIN_LIMIT):
    """Progress on the collection target: all test contracts + the first `limit` train contracts."""
    import statistics
    from rl_offline.common import model_slug
    print(f"Cache: {cache_root / model_slug(model)}   (max_rounds={max_rounds})")
    toks, remaining = [], 0
    train = collection_order(list_contracts("train"), target=test_label_mix())
    for part, cs in (("test", list_contracts("test")), ("train", train[:limit] if limit else train)):
        recs = [load_record(cache_path(c, model, cache_root)) for c in cs]
        done = [r for r in recs if is_complete(r, max_rounds)]
        partial = [r for r in recs if r and not is_complete(r, max_rounds)]
        for r in done:
            keys = required_keys(r["n_ta_batches"], max_rounds)
            toks.append(sum(r["calls"][k]["prompt_tokens"] + r["calls"][k]["completion_tokens"] for k in keys))
        remaining += len(cs) - len(done)
        print(f"  {part:<5}: {len(done):>3} complete, {len(partial):>3} partial, "
              f"{len(cs) - len(done) - len(partial):>3} not started  (of {len(cs)})")
    per = statistics.mean(toks) if toks else 40000
    src = "measured" if toks else "rough estimate"
    print(f"  tokens per contract ({src}): {per:,.0f}")
    print(f"  remaining: {remaining} contracts ≈ {remaining * per / 1e6:.1f}M tokens "
          f"≈ ${remaining * per * (0.85 * ME.PRICE_PER_M_INPUT + 0.15 * ME.PRICE_PER_M_OUTPUT) / 1e6:.2f} "
          f"at ${ME.PRICE_PER_M_INPUT}/${ME.PRICE_PER_M_OUTPUT} per M")


def main():
    from rl_offline.common import utf8_console
    utf8_console()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--part", choices=("test", "train", "all"), default="all")
    ap.add_argument("--limit", type=int, default=DEFAULT_TRAIN_LIMIT,
                    help=f"Max contracts of the train pool (default {DEFAULT_TRAIN_LIMIT}; 0 = all)")
    ap.add_argument("--max-rounds", type=int, default=MAX_ROUNDS)
    ap.add_argument("--cache-root", default=str(CACHE_ROOT))
    ap.add_argument("--backend", choices=("groq", "mock"), default="groq")
    ap.add_argument("--tpm", type=int, default=None, help="Tokens/min limit (default $GROQ_TPM or 8000)")
    ap.add_argument("--rpm", type=int, default=None, help="Requests/min limit (default $GROQ_RPM or 30)")
    ap.add_argument("--workers", type=int, default=1,
                    help="Contracts collected in parallel, each worker on its own key")
    ap.add_argument("--status", action="store_true", help="Show cache progress and exit")
    args = ap.parse_args()

    cache_root = Path(args.cache_root)
    if not cache_root.is_absolute():
        cache_root = ROOT / cache_root
    if args.status:
        status(ME.MODEL, cache_root, args.max_rounds, args.limit or None)
        return

    order: list[Contract] = []
    if args.part in ("test", "all"):
        order += collection_order(list_contracts("test"))
    if args.part in ("train", "all"):
        # train pool ordered to match the test split's label mix (gap: train/test shift)
        train = collection_order(list_contracts("train"), target=test_label_mix())
        order += train[: args.limit] if args.limit else train

    todo = [c for c in order if not is_complete(load_record(cache_path(c, ME.MODEL, cache_root)),
                                                 args.max_rounds)]
    if args.backend == "mock":
        table = {truncate(c.code(), ME.MAX_CODE_CHARS): c.label() for c in order}
        make = lambda: MockBackend(lambda user: next((t for code, t in table.items() if code in user), None))
        n_workers = 1
    else:
        pool = KeyPool(load_keys())
        n_workers = max(1, min(args.workers, len(pool.free), len(todo) or 1))
        make = lambda: GroqBackend(args.tpm, args.rpm, pool=pool)
        from collections import Counter
        mix = dict(Counter(p for _, p, _ in pool.free))
        print(f"{ME.MODEL}: keys {mix}, {n_workers} worker(s), "
              f"limiter {args.tpm or 'default'} tokens/min per key")
    print(f"{len(order) - len(todo)} of {len(order)} contracts already complete; {len(todo)} to go "
          f"(max_rounds={args.max_rounds}, cache={cache_root})")

    import queue, threading
    q: "queue.Queue[tuple[int, Contract]]" = queue.Queue()
    for i, c in enumerate(todo, 1):
        q.put((i, c))
    lock = threading.Lock()
    stop = threading.Event()
    totals = {"calls": 0, "tok": 0}
    started = time.time()

    def worker(wid: int):
        backend = make()
        failures = 0
        while not stop.is_set():
            try:
                i, c = q.get_nowait()
            except queue.Empty:
                return
            tag = f"[{i}/{len(todo)}] w{wid} {c.part:<5} {c.id} ({c.label()})"
            try:
                n, t = collect_contract(c, backend, cache_root, args.max_rounds)
            except DailyLimitReached as e:
                print(f"{tag}: daily limit on every remaining key — worker {wid} stops "
                      f"(progress saved).\n  {str(e)[:200]}", flush=True)
                return
            except AccessDenied as e:
                print(f"{tag}: access denied (key, VPN or network) — worker {wid} stops.\n  {e}", flush=True)
                return
            except CallFailed as e:
                failures += 1
                print(f"{tag}: incomplete ({e}) — will retry on the next run", flush=True)
                if failures >= 5:
                    print(f"worker {wid}: 5 contracts failed in a row; stopping.", flush=True)
                    return
                continue
            except RuntimeError as e:
                print(f"worker {wid}: {e}", flush=True)
                return
            failures = 0
            with lock:
                totals["calls"] += n
                totals["tok"] += t
            print(f"{tag}: ok  calls={n} tokens={t:,}", flush=True)

    threads = [threading.Thread(target=worker, args=(w,), daemon=True) for w in range(1, n_workers + 1)]
    for th in threads:
        th.start()
    try:
        while any(th.is_alive() for th in threads):
            for th in threads:
                th.join(timeout=0.5)
    except KeyboardInterrupt:
        stop.set()
        print("\nInterrupted — progress is saved (calls in flight are dropped).")
    tot_calls, tot_tok = totals["calls"], totals["tok"]
    mins = (time.time() - started) / 60
    print(f"\nThis run: {tot_calls} calls, {tot_tok:,} tokens, {mins:.1f} min")
    status(ME.MODEL, cache_root, args.max_rounds, args.limit or None)


if __name__ == "__main__":
    main()
