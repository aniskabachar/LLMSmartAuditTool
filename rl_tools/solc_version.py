"""Pick a solc version for a Solidity source from its pragma (used by rl_tools/run_slither.py)."""
from __future__ import annotations
import re

# newest release of each minor line (caret / range pragmas compile with these)
LATEST = {"0.4": "0.4.26", "0.5": "0.5.17", "0.6": "0.6.12", "0.7": "0.7.6", "0.8": "0.8.26"}


def pick_solc(code: str) -> str:
    m = re.search(r"pragma\s+solidity\s+([^;]+);", code)
    if not m:
        return LATEST["0.4"]
    spec = m.group(1).strip()
    exact = re.fullmatch(r"=?\s*(0\.\d+\.\d+)", spec)
    if exact:                                        # pinned version, e.g. "0.4.18"
        return exact.group(1)
    vers = re.findall(r"(0\.(\d+)\.(\d+))", spec)
    if not vers:
        return LATEST["0.4"]
    lo = vers[0]
    minor = f"0.{lo[1]}"
    if "<" in spec and len(vers) > 1:                # e.g. ">=0.4.22 <0.6.0": newest allowed line
        hi = vers[-1]
        upper_minor = int(hi[1]) - (1 if hi[2] == "0" else 0)
        minor = f"0.{max(int(lo[1]), upper_minor)}"
    return LATEST.get(minor, lo[0])
