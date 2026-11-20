"""
rl_offline/llm.py

LLM backends for the cache collector.

GroqBackend  — real calls, same model/temperature/max_tokens as verify/mode_evaluator.
               A client-side limiter keeps requests under the account's tokens-per-
               minute and requests-per-minute limits, so the run waits instead of
               triggering 429 storms. A daily-limit 429 (TPD/RPD) stops the run cleanly;
               it resumes from the cache next time.
MockBackend  — deterministic fake replies for testing the pipeline without an API key.
               Mock caches go to a separate directory and every output made from them
               is marked MOCK. Never report mock numbers.
"""

from __future__ import annotations
import hashlib, json, os, random, re, time
from collections import deque

from verify import mode_evaluator as ME


class DailyLimitReached(Exception):
    """Provider says the daily token/request quota is used up."""


class AccessDenied(Exception):
    """403 / auth problem: retrying will not help."""


class CallFailed(Exception):
    """Transient error that did not clear after retries."""


def _wait_seconds(msg: str) -> float | None:
    """Parse 'try again in 1h2m3.5s' / '7m12.5s' / '850ms' / '12.5s'."""
    m = re.search(r"try again in ((?:\d+h)?(?:\d+m(?!s))?(?:\d+(?:\.\d+)?s)?(?:\d+ms)?)", msg)
    if not m or not m.group(1):
        return None
    s = m.group(1)
    total = 0.0
    for val, unit in re.findall(r"(\d+(?:\.\d+)?)(ms|h|m|s)", s):
        total += float(val) * {"h": 3600, "m": 60, "s": 1, "ms": 0.001}[unit]
    return total


class _Limiter:
    """Sliding 60-second window over requests and (estimated) tokens."""

    def __init__(self, tpm: int, rpm: int):
        self.tpm, self.rpm = tpm, rpm
        self.events: deque[tuple[float, int]] = deque()

    def wait(self, est_tokens: int):
        est_tokens = min(est_tokens, self.tpm)
        while True:
            now = time.time()
            while self.events and now - self.events[0][0] > 60:
                self.events.popleft()
            used = sum(t for _, t in self.events)
            if used + est_tokens <= self.tpm and len(self.events) < self.rpm:
                return
            time.sleep(max(0.5, 60 - (now - self.events[0][0]) + 0.2))

    def record(self, tokens: int):
        self.events.append((time.time(), tokens))


# Providers serving the same open-weights model (openai/gpt-oss-120b). Every cached
# call records which provider answered it ("provider" field).
PROVIDERS = {
    #            env prefix          model id               tpm     rpm  tokens/day (soft cap)
    "groq":     ("GROQ_API_KEY",     "openai/gpt-oss-120b",  8_000, 30,  None),
    # NVIDIA API catalog (build.nvidia.com), free trial key, no card. NOTE: NVIDIA retired
    # openai/gpt-oss-120b on 2026-09-03 (HTTP 410); the backend skips its keys while that holds.
    # Limits are not
    # published: conservative defaults, override with NVIDIA_TPM / NVIDIA_RPM / NVIDIA_MODEL.
    "nvidia":   ("NVIDIA_API_KEY",   "openai/gpt-oss-120b", 60_000, 30,  None),
    # Cerebras free trial: 5 RPM, 30K uncached TPM, 1M TPD ($5 credit, expires after 30 days)
    "cerebras": ("CEREBRAS_API_KEY", "gpt-oss-120b",        30_000,  5,  1_000_000),
}
# OpenAI-compatible chat-completions endpoints (Groq uses its own SDK)
ENDPOINTS = {
    "nvidia":   "https://integrate.api.nvidia.com/v1/chat/completions",
    "cerebras": "https://api.cerebras.ai/v1/chat/completions",
}


def load_keys() -> list[tuple[str, str]]:
    """(provider, key) pairs, in PROVIDERS order (Groq, then NVIDIA, then Cerebras):
    <PREFIX>, <PREFIX>_2 ... <PREFIX>_20 from the environment / .env."""
    import verify.real_evaluator  # noqa: F401  (loads .env)
    out = []
    for prov, (prefix, *_rest) in PROVIDERS.items():
        names = [prefix] + [f"{prefix}_{i}" for i in range(2, 21)]
        out += [(prov, os.getenv(n)) for n in names if os.getenv(n)]
    return out


class KeyPool:
    """Thread-safe queue of unused (provider, key) pairs shared by parallel workers."""

    def __init__(self, keys: list[tuple[str, str]]):
        import threading
        self.lock = threading.Lock()
        self.free = [(i, prov, k) for i, (prov, k) in enumerate(keys, 1)]

    def take(self) -> tuple[int, str, str] | None:
        with self.lock:
            return self.free.pop(0) if self.free else None


class _HTTPError(Exception):
    pass


def _openai_compat_create(provider: str, key: str, model: str, system: str, user: str) -> dict:
    import httpx
    r = httpx.post(ENDPOINTS[provider], timeout=180, headers={"Authorization": f"Bearer {key}"}, json={
        "model": model, "temperature": ME.TEMPERATURE, "max_completion_tokens": ME.MAX_TOKENS,
        "reasoning_effort": ME.REASONING_EFFORT,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
    if r.status_code != 200:
        ra = r.headers.get("retry-after")
        hdr = " ".join(f"{k}={v}" for k, v in r.headers.items() if k.lower().startswith("x-ratelimit"))
        raise _HTTPError(f"Error code: {r.status_code} - {r.text[:400]} retry-after={ra} {hdr}")
    d = r.json()
    ch, u = d["choices"][0], d.get("usage") or {}
    return {"raw": (ch.get("message") or {}).get("content") or "", "finish_reason": ch.get("finish_reason"),
            "prompt_tokens": u.get("prompt_tokens", 0) or 0, "completion_tokens": u.get("completion_tokens", 0) or 0}


class GroqBackend:
    """Real API backend (Groq first, then NVIDIA / Cerebras as fallbacks). `name` stays "groq" so
    existing cache files remain compatible; each call records its provider."""
    name = "groq"

    def __init__(self, tpm: int | None = None, rpm: int | None = None,
                 max_wait: float = 300.0, retries: int = 6, pool: KeyPool | None = None):
        self.tpm_override, self.rpm_override = tpm, rpm
        self.max_wait, self.retries = max_wait, retries
        self.model = ME.MODEL
        # Keys come from a pool: when the current key hits its daily limit the backend
        # takes the next unused one; DailyLimitReached only once the pool is empty.
        self.pool = pool or KeyPool(load_keys())
        self._set_key(self.pool.take() or (0, "groq", None))

    def _set_key(self, entry):
        self.key_no, self.provider, self.key = entry
        _, self.api_model, tpm, rpm, self.tpd = PROVIDERS[self.provider]
        env = PROVIDERS[self.provider][0].removesuffix("_API_KEY")
        self.api_model = os.getenv(f"{env}_MODEL", self.api_model)
        if self.provider == "groq":
            tpm = self.tpm_override or int(os.getenv("GROQ_TPM", tpm))
            rpm = self.rpm_override or int(os.getenv("GROQ_RPM", rpm))
        else:
            tpm = int(os.getenv(f"{env}_TPM", tpm))
            rpm = int(os.getenv(f"{env}_RPM", rpm))
        self.tpm, self.rpm = tpm, rpm
        self.limiter = _Limiter(tpm, rpm)
        self.used_today = 0
        self._client = None

    def _create(self, system: str, user: str) -> dict:
        if not self.key:
            raise RuntimeError("No API key set (or every key is used up). Add GROQ_API_KEY to .env.")
        if self.provider in ENDPOINTS:
            return _openai_compat_create(self.provider, self.key, self.api_model, system, user)
        if self._client is None:
            from groq import Groq
            self._client = Groq(api_key=self.key)
        r = self._client.chat.completions.create(
            model=self.api_model, temperature=ME.TEMPERATURE, max_tokens=ME.MAX_TOKENS,
            reasoning_effort=ME.REASONING_EFFORT,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
        usage = getattr(r, "usage", None)
        return {"raw": r.choices[0].message.content or "", "finish_reason": r.choices[0].finish_reason,
                "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
                "completion_tokens": getattr(usage, "completion_tokens", 0) or 0}

    def _next_key(self) -> bool:
        nxt = self.pool.take()
        if not nxt:
            return False
        old = f"{self.provider} key {self.key_no}"
        self._set_key(nxt)
        print(f"      daily limit on {old}, switching to {self.provider} key {self.key_no}", flush=True)
        return True

    def call(self, system: str, user: str) -> dict:
        est = int((len(system) + len(user)) / 3.2) + ME.MAX_TOKENS
        last = ""
        attempt = 0
        minute_429s = 0
        while attempt < self.retries:
            attempt += 1
            if self.tpd and self.used_today + est > self.tpd:       # soft daily cap (Cerebras)
                if self._next_key():
                    attempt = minute_429s = 0
                    continue
                raise DailyLimitReached(f"{self.provider} daily token budget used")
            self.limiter.wait(est)
            try:
                t_call = time.time()
                out = self._create(system, user)
                out["seconds"] = round(time.time() - t_call, 2)   # API latency only, no limiter waits
            except RuntimeError:
                raise                                   # missing API key
            except Exception as e:
                msg = str(e)
                last = msg
                self.limiter.record(est // 2)
                low = msg.lower()
                if "403" in msg or "access denied" in low or "401" in msg or "invalid api key" in low:
                    raise AccessDenied(msg[:300])
                if ("410" in msg or "no longer available" in low or "end of life" in low
                        or "model_not_found" in low or ("404" in msg and "model" in low)):
                    # this provider no longer serves the model: skip all its keys
                    print(f"      {self.provider} does not serve {self.api_model} any more; skipping it",
                          flush=True)
                    dead = self.provider
                    while self.provider == dead:
                        if not self._next_key():
                            raise DailyLimitReached(f"{dead}: model unavailable, no other keys left")
                    attempt = minute_429s = 0
                    continue
                if "402" in msg or "insufficient" in low or "credit" in low:   # trial credit gone
                    if self._next_key():
                        attempt = minute_429s = 0
                        continue
                    raise DailyLimitReached(msg[:300])
                if "429" in msg or "rate_limit" in low or "rate limit" in low or "too many" in low:
                    wait = _wait_seconds(msg)
                    m = re.search(r"retry-after=(\d+(?:\.\d+)?)", msg)
                    if wait is None and m:
                        wait = float(m.group(1))
                    minute_429s += 1
                    daily = ("per day" in low or "(tpd)" in low or "(rpd)" in low
                             or "tokens_per_day" in low or "daily" in low or "per_day" in low)
                    # Cerebras / NVIDIA do not document how a daily 429 looks: after 6
                    # rate-limit refusals in a row on one call, treat the key as used up.
                    cerebras_out = self.provider != "groq" and minute_429s >= 6
                    if daily or (wait is not None and wait > self.max_wait) or cerebras_out:
                        if self._next_key():
                            attempt = minute_429s = 0
                            continue
                        raise DailyLimitReached(msg[:300])
                    wait = (wait or 10.0 * minute_429s) + 1.0
                    print(f"      rate limit ({self.provider}), waiting {wait:.0f}s", flush=True)
                    time.sleep(wait)
                    if self.provider != "groq":
                        attempt -= 1                     # per-minute refusals are not failed tries
                    continue
                if "context_length" in low or "too large" in low or "413" in msg:
                    raise CallFailed(msg[:300])          # will not succeed on retry
                time.sleep(min(60, 5 * 2 ** (attempt - 1)))
                continue
            tok = out["prompt_tokens"] + out["completion_tokens"]
            self.limiter.record(tok)
            self.used_today += tok
            out["provider"] = self.provider
            return out
        raise CallFailed(last[:300] or "retries exhausted")


class MockBackend:
    """
    Deterministic stand-in for pipeline tests. Reply quality depends on the mode
    (from the system prompt), the round and the true label hidden in the prompt's
    contract, so the RL code has some structure to learn — but it is NOT real data.
    """
    name = "mock"

    def __init__(self, label_lookup):
        self.label_lookup = label_lookup   # code text -> true type
        self.model = ME.MODEL

    def call(self, system: str, user: str) -> dict:
        seed = int(hashlib.sha256((system + user).encode()).hexdigest()[:12], 16)
        rng = random.Random(seed)
        truth = self.label_lookup(user)
        review = system.startswith("You are a senior")
        ta = "TARGETED" in system
        p_hit = 0.55 if ta else 0.65
        if review:
            prev = re.findall(r'"type": "([a-z_]+)"', user)
            types = set(prev)
            if truth and truth not in types and rng.random() < 0.25:
                types.add(truth)
            for t in list(types):
                if t != truth and rng.random() < 0.35:
                    types.discard(t)
        else:
            types = set()
            if truth and rng.random() < p_hit:
                types.add(truth)
            for _ in range(rng.choice([0, 1, 1, 2] if ta else [0, 0, 1])):
                types.add(rng.choice(["reentrancy", "integer_overflow", "access_control",
                                      "timestamp_dependence", "unchecked_low_level_calls",
                                      "denial_of_service", "front_running"]))
        reply = {"vulnerabilities": [{"type": t, "location": f"fn_{i}", "severity": "medium",
                                      "explanation": "mock"} for i, t in enumerate(sorted(types))],
                 "confidence": round(rng.uniform(0.5, 0.95), 2)}
        pt = int((len(system) + len(user)) / 3.6)
        return {"raw": json.dumps(reply), "prompt_tokens": pt,
                "completion_tokens": rng.randint(150, 700), "finish_reason": "stop"}
