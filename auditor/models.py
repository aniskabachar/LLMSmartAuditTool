"""
auditor/models.py

Pydantic models shared across grader, environment and evaluator.
"""
from __future__ import annotations
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field


class VulnerabilityType(str, Enum):
    reentrancy                = "reentrancy"
    integer_overflow          = "integer_overflow"
    access_control            = "access_control"
    tx_origin                 = "tx_origin"
    timestamp_dependence      = "timestamp_dependence"
    selfdestruct              = "selfdestruct"
    uninitialized_storage     = "uninitialized_storage"
    unchecked_low_level_calls = "unchecked_low_level_calls"
    bad_randomness            = "bad_randomness"
    front_running             = "front_running"
    denial_of_service         = "denial_of_service"
    other                     = "other"


class Severity(str, Enum):
    low      = "low"
    medium   = "medium"
    high     = "high"
    critical = "critical"


class DetectedVulnerability(BaseModel):
    type:        VulnerabilityType
    location:    str  = "unknown"
    severity:    Severity = Severity.medium
    explanation: str  = ""


class Action(BaseModel):
    """Output of the LLM agent — list of detected vulnerabilities."""
    vulnerabilities: List[DetectedVulnerability] = Field(default_factory=list)


class GroundTruthVuln(BaseModel):
    type:        str
    severity:    str = "medium"
    location:    str = "unknown"
    description: str = ""


class GroundTruth(BaseModel):
    contract:        str
    source:          str = "SmartBugs"
    category:        str = ""
    vulnerabilities: List[GroundTruthVuln] = Field(default_factory=list)
