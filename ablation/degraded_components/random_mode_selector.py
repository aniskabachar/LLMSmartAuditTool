"""
ablation/degraded_components/random_mode_selector.py

Drop-in replacement for the trained PPO Mode Selector.
Selects audit mode (BA / TA / Hybrid) uniformly at random.
Interface is identical to the trained policy so the orchestrator
needs zero modification to swap it in.
"""
import random


class RandomModeSelector:
    """
    Ablation stand-in for PPO mode selector.

    Mode mapping (matches PPOModeSelector convention):
        0 → Basic Analysis (BA)
        1 → Template Analysis (TA)
        2 → Hybrid
    """

    MODE_NAMES = {0: "BA", 1: "TA", 2: "Hybrid"}

    def __init__(self, seed: int | None = None):
        self._rng = random.Random(seed)

    # ------------------------------------------------------------------
    # Public API — identical to stable-baselines3 policy.predict()
    # ------------------------------------------------------------------

    def predict(self, state, deterministic: bool = False):
        """
        Parameters
        ----------
        state       : ignored (random selector doesn't use observations)
        deterministic : ignored (always random)

        Returns
        -------
        (action, state)   where action ∈ {0, 1, 2}
        """
        mode = self._rng.randint(0, 2)
        return mode, None

    def load(self, path: str):
        """No-op — no checkpoint to load for a random policy."""
        pass

    def get_mode_name(self, action: int) -> str:
        return self.MODE_NAMES.get(action, "unknown")
