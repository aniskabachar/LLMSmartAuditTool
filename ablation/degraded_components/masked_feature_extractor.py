"""
ablation/degraded_components/masked_feature_extractor.py

Wraps the real ContractAnalyzer (or any extractor with an .extract() method).
When mask_new=True, zeroes out feature positions 22–27 (0-indexed),
which correspond to the 6 new features added in v2 (1-indexed positions 23–28).

The state vector remains 33-dimensional for full encoder compatibility —
the masking only suppresses the information carried by those positions,
simulating the v1 feature set without any architectural changes.
"""
import numpy as np


class MaskedFeatureExtractor:
    """
    Decorator around any contract feature extractor.

    Attributes
    ----------
    base        : extractor with .extract(contract_path) → np.ndarray (33,)
    mask_new    : if True, zero out v2 positions [22:28] (0-indexed)
    NEW_FEAT_SLICE : slice covering the 6 new v2 features
    """

    NEW_FEAT_SLICE = slice(22, 28)   # 0-indexed positions 22–27 inclusive

    def __init__(self, base_extractor, mask_new: bool = False):
        """
        Parameters
        ----------
        base_extractor : object
            Must expose .extract(contract_path: str) -> np.ndarray of shape (33,)
        mask_new : bool
            When True, zero out the 6 new features introduced in v2.
        """
        self.base = base_extractor
        self.mask_new = mask_new

    def extract(self, contract_path: str) -> np.ndarray:
        """
        Extract features from a Solidity contract file.

        Parameters
        ----------
        contract_path : str
            Absolute or relative path to the .sol file.

        Returns
        -------
        np.ndarray, shape (33,)
            Feature vector with new positions zeroed when mask_new=True.
        """
        features = self.base.extract(contract_path)   # expected shape: (33,)
        features = np.asarray(features, dtype=np.float32).copy()

        if features.shape[0] != 33:
            raise ValueError(
                f"Expected 33-dim feature vector from base extractor, "
                f"got shape {features.shape}."
            )

        if self.mask_new:
            features[self.NEW_FEAT_SLICE] = 0.0

        return features

    # Delegate any other attribute access to the base extractor
    def __getattr__(self, name):
        return getattr(self.base, name)
