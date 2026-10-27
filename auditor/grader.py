"""
auditor/grader.py

Real precision/recall grader against SmartBugs ground truth labels.

Matching logic:
  - True positive  : agent found a vuln type that exists in ground truth
  - False positive : agent reported a type NOT in ground truth
  - Missed bug     : ground truth vuln type NOT found by agent

Score = 0.6 * recall + 0.4 * precision
  (weighted toward recall because missing bugs is worse than false alarms
   in a security context)
"""
from __future__ import annotations
from typing import Union
import json
from pathlib import Path

from .models import Action, GroundTruth


# Synonym groups: types that are considered equivalent for matching
SYNONYMS: dict[str, set[str]] = {
    "integer_overflow": {"arithmetic", "integer_overflow", "overflow", "underflow"},
    "reentrancy":       {"reentrancy", "re-entrancy"},
    "access_control":   {"access_control", "authorization", "ownership"},
    "timestamp_dependence": {"timestamp_dependence", "time_manipulation",
                             "block_timestamp"},
    "unchecked_low_level_calls": {"unchecked_low_level_calls", "unchecked_calls",
                                   "low_level_calls"},
    "other":            {"other", "short_addresses", "miscellaneous"},
}

def _normalize_type(vuln_type: str) -> str:
    """Collapse synonyms to canonical type name."""
    t = vuln_type.lower().strip()
    for canonical, variants in SYNONYMS.items():
        if t in variants:
            return canonical
    return t


def grade(action: Action, ground_truth: Union[dict, GroundTruth]) -> dict:
    """
    Grade an agent's Action against ground truth.

    Parameters
    ----------
    action       : Action with .vulnerabilities list
    ground_truth : dict (raw JSON) or GroundTruth pydantic model

    Returns
    -------
    dict with keys:
        score, precision, recall, f1,
        true_positives, false_positives, missed_bugs,
        found_types, expected_types
    """
    if isinstance(ground_truth, dict):
        gt = GroundTruth(**ground_truth)
    else:
        gt = ground_truth

    # Build expected type set (normalised)
    expected_types = {_normalize_type(v.type) for v in gt.vulnerabilities}

    # Build found type set (normalised)
    found_types = {_normalize_type(v.type) for v in action.vulnerabilities}

    # True positives: found ∩ expected
    true_positives  = found_types & expected_types
    false_positives = found_types - expected_types
    missed          = expected_types - found_types

    n_tp = len(true_positives)
    n_fp = len(false_positives)
    n_fn = len(missed)

    precision = n_tp / (n_tp + n_fp) if (n_tp + n_fp) > 0 else 0.0
    recall    = n_tp / (n_tp + n_fn) if (n_tp + n_fn) > 0 else 0.0
    f1        = (2 * precision * recall / (precision + recall)
                 if (precision + recall) > 0 else 0.0)

    # Weighted score: recall-heavy for security context
    score = round(0.6 * recall + 0.4 * precision, 4)

    return {
        "score":          score,
        "precision":      round(precision, 4),
        "recall":         round(recall, 4),
        "f1":             round(f1, 4),
        "true_positives":  n_tp,
        "false_positives": n_fp,
        "missed_bugs":     n_fn,
        "found_types":    sorted(found_types),
        "expected_types": sorted(expected_types),
    }


def grade_from_files(sol_path: Path, gt_path: Path, action: Action) -> dict:
    """Convenience wrapper that loads ground truth from disk."""
    ground_truth = json.loads(gt_path.read_text(encoding="utf-8"))
    result = grade(action, ground_truth)
    result["contract"] = sol_path.name
    return result
