"""
ablation/degraded_components/fixed_stopping_policy.py

Drop-in replacement for the trained DQN Stopping Policy.
Always runs exactly `fixed_n` rounds before issuing a STOP signal.
Interface is identical to DQNStoppingPolicy so the orchestrator
needs zero modification to swap it in.
"""


class FixedStoppingPolicy:
    """
    Ablation stand-in for DQN stopping policy.

    Action mapping (matches DQNStoppingPolicy convention):
        0 → CONTINUE
        1 → STOP
    """

    def __init__(self, fixed_n: int = 3):
        """
        Parameters
        ----------
        fixed_n : int
            Number of rounds to run before stopping. Default is 3,
            matching the v1 system's baseline stopping behaviour.
        """
        if fixed_n < 1:
            raise ValueError(f"fixed_n must be >= 1, got {fixed_n}")
        self.fixed_n = fixed_n
        self.current_round = 0

    # ------------------------------------------------------------------
    # Public API — identical to stable-baselines3 policy.predict()
    # ------------------------------------------------------------------

    def predict(self, state, deterministic: bool = False):
        """
        Parameters
        ----------
        state       : ignored (fixed policy doesn't use observations)
        deterministic : ignored (always deterministic by design)

        Returns
        -------
        (action, state)  where action ∈ {0=CONTINUE, 1=STOP}
        """
        self.current_round += 1
        if self.current_round >= self.fixed_n:
            self.current_round = 0
            return 1, None   # STOP
        return 0, None       # CONTINUE

    def reset(self):
        """Reset round counter. Call between contracts."""
        self.current_round = 0

    def load(self, path: str):
        """No-op — no checkpoint to load for a fixed policy."""
        pass
