"""
Dataset Validation and Quality Assurance
========================================

This module provides comprehensive validation and quality assurance for
benchmark datasets to ensure consistency with the original LLM-SmartAudit
paper's evaluation methodology.

Key Features:
1. Data integrity verification and consistency checks
2. Vulnerability annotation validation
3. Statistical analysis and distribution validation
4. Cross-dataset compatibility verification
5. Benchmark comparison with paper results
6. Dataset versioning and reproducibility checks
"""

import os
import json
import logging
import numpy as np
import pandas as pd
from typing import Dict, List, Tuple, Optional, Any, Set
from dataclasses import dataclass
from pathlib import Path
import matplotlib.pyplot as plt
import seaborn as sns
from datetime import datetime

# Import from our dataset acquisition module
import sys
sys.path.append(os.path.dirname(__file__))
from dataset_acquisition import ContractInfo, DatasetMetadata

@dataclass
class ValidationResult:
    """Result of dataset validation"""
    dataset_name: str
    is_valid: bool
    validation_score: float  # 0.0 to 1.0
    issues: List[str]
    warnings: List[str]
    statistics: Dict[str, Any]
    
@dataclass
class QualityMetrics:
    """Quality metrics for dataset assessment"""
    completeness: float  # Data completeness score
    consistency: float   # Internal consistency score
    diversity: float     # Vulnerability type diversity
    coverage: float      # Coverage of vulnerability spectrum
    balance: float       # Class balance for ML training
    
class DatasetValidator:
    """
    Comprehensive dataset validation and quality assurance
    
    Validates datasets against quality criteria and paper benchmarks.
    """
    
    def __init__(self, data_dir: str = "./datasets", output_dir: str = "./validation"):
        self.data_dir = Path(data_dir)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        self.logger = logging.getLogger(__name__)
        
        # Expected paper benchmarks for validation
        self.paper_benchmarks = {
            "common_vulnerability_set": {
                "expected_size": 110,
                "accuracy_ba": 0.65,
                "accuracy_ta": 0.90,
                "accuracy_hybrid": 0.88,
                "cost_ba": 0.21,
                "cost_ta": 0.98,
                "cost_hybrid": 1.19
            },
            "real_world_set": {
                "expected_size": 6454,
                "expected_projects": 102,
                "accuracy_ba": 0.476,
                "accuracy_ta": 0.35,  # Lower on real-world
                "accuracy_hybrid": 0.623
            }
        }
        
        # Vulnerability type taxonomy for validation
        self.expected_vulnerability_types = {
            "access_control", "arithmetic", "reentrancy", "unchecked_calls",
            "denial_of_service", "time_manipulation", "bad_randomness",
            "front_running", "short_addresses", "unknown_unknowns"
        }
        
        # Quality thresholds
        self.quality_thresholds = {
            "min_completeness": 0.95,
            "min_consistency": 0.90,
            "min_diversity": 0.70,
            "min_coverage": 0.80,
            "min_balance": 0.30  # Minimum class should be at least 30% of maximum
        }
    
    def validate_dataset(self, dataset_name: str) -> ValidationResult:
        """
        Perform comprehensive validation of a dataset
        
        Args:
            dataset_name: Name of dataset to validate
            
        Returns:
            ValidationResult with validation outcome and details
        """
        
        self.logger.info(f"Validating dataset: {dataset_name}")
        
        # Load dataset
        contracts, metadata = self._load_dataset_with_metadata(dataset_name)
        
        if not contracts:
            return ValidationResult(
                dataset_name=dataset_name,
                is_valid=False,
                validation_score=0.0,
                issues=[f"Dataset {dataset_name} not found or empty"],
                warnings=[],
                statistics={}
            )
        
        issues = []
        warnings = []
        
        # 1. Basic integrity checks
        integrity_issues = self._check_data_integrity(contracts, metadata)
        issues.extend(integrity_issues)
        
        # 2. Size and structure validation
        size_issues, size_warnings = self._check_size_requirements(contracts, metadata, dataset_name)
        issues.extend(size_issues)
        warnings.extend(size_warnings)
        
        # 3. Vulnerability annotation validation
        vuln_issues, vuln_warnings = self._check_vulnerability_annotations(contracts)
        issues.extend(vuln_issues)
        warnings.extend(vuln_warnings)
        
        # 4. Statistical distribution validation
        stats_issues, stats_warnings = self._check_statistical_distributions(contracts)
        issues.extend(stats_issues)
        warnings.extend(stats_warnings)
        
        # 5. Quality metrics calculation
        quality_metrics = self._calculate_quality_metrics(contracts)
        quality_issues, quality_warnings = self._validate_quality_metrics(quality_metrics)
        issues.extend(quality_issues)
        warnings.extend(quality_warnings)
        
        # 6. Paper benchmark comparison
        benchmark_issues, benchmark_warnings = self._compare_with_paper_benchmarks(
            contracts, metadata, dataset_name
        )
        issues.extend(benchmark_issues)
        warnings.extend(benchmark_warnings)
        
        # Calculate overall validation score
        validation_score = self._calculate_validation_score(quality_metrics, len(issues), len(warnings))
        
        # Determine if dataset is valid
        is_valid = len(issues) == 0 and validation_score >= 0.7
        
        # Compile statistics
        statistics = {
            "contract_count": len(contracts),
            "vulnerability_count": sum(len(c.vulnerability_labels) for c in contracts),
            "quality_metrics": {
                "completeness": quality_metrics.completeness,
                "consistency": quality_metrics.consistency,
                "diversity": quality_metrics.diversity,
                "coverage": quality_metrics.coverage,
                "balance": quality_metrics.balance
            },
            "validation_score": validation_score
        }
        
        result = ValidationResult(
            dataset_name=dataset_name,
            is_valid=is_valid,
            validation_score=validation_score,
            issues=issues,
            warnings=warnings,
            statistics=statistics
        )
        
        # Save validation report
        self._save_validation_report(result, contracts)
        
        return result
    
    def _load_dataset_with_metadata(self, dataset_name: str) -> Tuple[List[ContractInfo], Optional[DatasetMetadata]]:
        """Load dataset with its metadata"""
        
        dataset_dir = self.data_dir / "processed" / dataset_name
        
        # Load contracts
        contracts_file = dataset_dir / "contracts.json"
        metadata_file = dataset_dir / "metadata.json"
        
        contracts = []
        metadata = None
        
        if contracts_file.exists():
            with open(contracts_file, 'r') as f:
                contracts_data = json.load(f)
            contracts = [ContractInfo(**data) for data in contracts_data]
        
        if metadata_file.exists():
            with open(metadata_file, 'r') as f:
                metadata_data = json.load(f)
            metadata = DatasetMetadata(**metadata_data)
        
        return contracts, metadata
    
    def _check_data_integrity(self, contracts: List[ContractInfo], metadata: Optional[DatasetMetadata]) -> List[str]:
        """Check basic data integrity"""
        
        issues = []
        
        # Check for duplicate contract IDs
        contract_ids = [c.contract_id for c in contracts]
        if len(contract_ids) != len(set(contract_ids)):
            issues.append("Duplicate contract IDs found in dataset")
        
        # Check for empty or invalid contracts
        empty_contracts = [c for c in contracts if not c.contract_id or c.lines_of_code <= 0]
        if empty_contracts:
            issues.append(f"{len(empty_contracts)} contracts have invalid or empty data")
        
        # Check metadata consistency
        if metadata:
            if metadata.total_contracts != len(contracts):
                issues.append(f"Metadata contract count ({metadata.total_contracts}) doesn't match actual count ({len(contracts)})")
            
            actual_vuln_count = sum(len(c.vulnerability_labels) for c in contracts)
            if metadata.vulnerability_count != actual_vuln_count:
                issues.append(f"Metadata vulnerability count ({metadata.vulnerability_count}) doesn't match actual count ({actual_vuln_count})")
        
        return issues
    
    def _check_size_requirements(self, 
                               contracts: List[ContractInfo], 
                               metadata: Optional[DatasetMetadata], 
                               dataset_name: str) -> Tuple[List[str], List[str]]:
        """Check if dataset meets size requirements"""
        
        issues = []
        warnings = []
        
        if dataset_name in self.paper_benchmarks:
            expected_size = self.paper_benchmarks[dataset_name]["expected_size"]
            actual_size = len(contracts)
            
            size_ratio = actual_size / expected_size
            
            if size_ratio < 0.8:  # Less than 80% of expected size
                issues.append(f"Dataset too small: {actual_size} contracts, expected ~{expected_size}")
            elif size_ratio < 0.95:  # Less than 95% of expected size
                warnings.append(f"Dataset smaller than expected: {actual_size} contracts, expected {expected_size}")
            elif size_ratio > 1.2:  # More than 120% of expected size
                warnings.append(f"Dataset larger than expected: {actual_size} contracts, expected {expected_size}")
        
        # Check minimum viable size
        min_size = 50
        if len(contracts) < min_size:
            issues.append(f"Dataset too small for reliable evaluation: {len(contracts)} contracts, minimum {min_size}")
        
        return issues, warnings
    
    def _check_vulnerability_annotations(self, contracts: List[ContractInfo]) -> Tuple[List[str], List[str]]:
        """Check vulnerability annotation quality"""
        
        issues = []
        warnings = []
        
        # Check vulnerability type validity
        all_vuln_types = set()
        for contract in contracts:
            all_vuln_types.update(contract.vulnerability_labels)
        
        unknown_types = all_vuln_types - self.expected_vulnerability_types
        if unknown_types:
            warnings.append(f"Unknown vulnerability types found: {unknown_types}")
        
        # Check annotation completeness
        contracts_without_annotations = [c for c in contracts if not c.vulnerability_labels]
        if len(contracts_without_annotations) / len(contracts) > 0.3:  # More than 30% without annotations
            warnings.append(f"{len(contracts_without_annotations)} contracts have no vulnerability annotations")
        
        # Check severity level consistency
        all_severities = set()
        for contract in contracts:
            all_severities.update(contract.severity_levels)
        
        expected_severities = {"High", "Medium", "Low", "Info"}
        unknown_severities = all_severities - expected_severities
        if unknown_severities:
            warnings.append(f"Non-standard severity levels found: {unknown_severities}")
        
        # Check vulnerability-severity alignment
        misaligned_contracts = []
        for contract in contracts:
            if len(contract.vulnerability_labels) != len(contract.severity_levels):
                misaligned_contracts.append(contract.contract_id)
        
        if misaligned_contracts:
            issues.append(f"{len(misaligned_contracts)} contracts have misaligned vulnerability labels and severity levels")
        
        return issues, warnings
    
    def _check_statistical_distributions(self, contracts: List[ContractInfo]) -> Tuple[List[str], List[str]]:
        """Check if statistical distributions are reasonable"""
        
        issues = []
        warnings = []
        
        # Check lines of code distribution
        loc_values = [c.lines_of_code for c in contracts]
        loc_mean = np.mean(loc_values)
        loc_median = np.median(loc_values)
        
        # Check for unrealistic values
        if np.min(loc_values) <= 0:
            issues.append("Some contracts have zero or negative lines of code")
        
        if np.max(loc_values) > 10000:
            warnings.append(f"Some contracts are very large (max: {np.max(loc_values)} LOC)")
        
        if loc_mean < 50:
            warnings.append(f"Average contract size seems small: {loc_mean:.1f} LOC")
        
        # Check vulnerability distribution
        vuln_counts = {}
        for contract in contracts:
            for vuln in contract.vulnerability_labels:
                vuln_counts[vuln] = vuln_counts.get(vuln, 0) + 1
        
        # Check for extremely imbalanced classes
        if vuln_counts:
            max_count = max(vuln_counts.values())
            min_count = min(vuln_counts.values())
            
            if max_count / min_count > 50:  # 50:1 imbalance
                warnings.append(f"Severe class imbalance detected (ratio: {max_count/min_count:.1f}:1)")
        
        return issues, warnings
    
    def _calculate_quality_metrics(self, contracts: List[ContractInfo]) -> QualityMetrics:
        """Calculate comprehensive quality metrics"""
        
        # Completeness: fraction of contracts with complete information
        complete_contracts = 0
        for contract in contracts:
            is_complete = (
                bool(contract.contract_id) and
                contract.lines_of_code > 0 and
                bool(contract.vulnerability_labels) and
                len(contract.vulnerability_labels) == len(contract.severity_levels)
            )
            if is_complete:
                complete_contracts += 1
        
        completeness = complete_contracts / len(contracts) if contracts else 0
        
        # Consistency: internal consistency of annotations
        consistent_contracts = 0
        for contract in contracts:
            # Check if vulnerability labels and severity levels are aligned
            is_consistent = len(contract.vulnerability_labels) == len(contract.severity_levels)
            if is_consistent:
                consistent_contracts += 1
        
        consistency = consistent_contracts / len(contracts) if contracts else 0
        
        # Diversity: variety of vulnerability types
        all_vuln_types = set()
        for contract in contracts:
            all_vuln_types.update(contract.vulnerability_labels)
        
        diversity = len(all_vuln_types) / len(self.expected_vulnerability_types)
        
        # Coverage: how well the dataset covers the vulnerability spectrum
        covered_types = all_vuln_types.intersection(self.expected_vulnerability_types)
        coverage = len(covered_types) / len(self.expected_vulnerability_types)
        
        # Balance: class balance for ML training
        vuln_counts = {}
        for contract in contracts:
            for vuln in contract.vulnerability_labels:
                vuln_counts[vuln] = vuln_counts.get(vuln, 0) + 1
        
        if vuln_counts:
            max_count = max(vuln_counts.values())
            min_count = min(vuln_counts.values())
            balance = min_count / max_count
        else:
            balance = 1.0
        
        return QualityMetrics(
            completeness=completeness,
            consistency=consistency,
            diversity=diversity,
            coverage=coverage,
            balance=balance
        )
    
    def _validate_quality_metrics(self, metrics: QualityMetrics) -> Tuple[List[str], List[str]]:
        """Validate quality metrics against thresholds"""
        
        issues = []
        warnings = []
        
        if metrics.completeness < self.quality_thresholds["min_completeness"]:
            issues.append(f"Low data completeness: {metrics.completeness:.3f} < {self.quality_thresholds['min_completeness']}")
        
        if metrics.consistency < self.quality_thresholds["min_consistency"]:
            issues.append(f"Low data consistency: {metrics.consistency:.3f} < {self.quality_thresholds['min_consistency']}")
        
        if metrics.diversity < self.quality_thresholds["min_diversity"]:
            warnings.append(f"Low vulnerability diversity: {metrics.diversity:.3f} < {self.quality_thresholds['min_diversity']}")
        
        if metrics.coverage < self.quality_thresholds["min_coverage"]:
            warnings.append(f"Low vulnerability coverage: {metrics.coverage:.3f} < {self.quality_thresholds['min_coverage']}")
        
        if metrics.balance < self.quality_thresholds["min_balance"]:
            warnings.append(f"Poor class balance: {metrics.balance:.3f} < {self.quality_thresholds['min_balance']}")
        
        return issues, warnings
    
    def _compare_with_paper_benchmarks(self, 
                                     contracts: List[ContractInfo], 
                                     metadata: Optional[DatasetMetadata],
                                     dataset_name: str) -> Tuple[List[str], List[str]]:
        """Compare dataset characteristics with paper benchmarks"""
        
        issues = []
        warnings = []
        
        if dataset_name not in self.paper_benchmarks:
            return issues, warnings
        
        benchmark = self.paper_benchmarks[dataset_name]
        
        # Check contract count
        if "expected_size" in benchmark:
            expected = benchmark["expected_size"]
            actual = len(contracts)
            
            if abs(actual - expected) / expected > 0.1:  # More than 10% deviation
                warnings.append(f"Contract count deviates from paper: {actual} vs expected {expected}")
        
        # Check project count for real-world dataset
        if "expected_projects" in benchmark and dataset_name == "real_world_set":
            unique_projects = len(set(c.project_name for c in contracts))
            expected_projects = benchmark["expected_projects"]
            
            if abs(unique_projects - expected_projects) / expected_projects > 0.1:
                warnings.append(f"Project count deviates from paper: {unique_projects} vs expected {expected_projects}")
        
        return issues, warnings
    
    def _calculate_validation_score(self, metrics: QualityMetrics, num_issues: int, num_warnings: int) -> float:
        """Calculate overall validation score"""
        
        # Base score from quality metrics (weighted average)
        base_score = (
            metrics.completeness * 0.25 +
            metrics.consistency * 0.25 +
            metrics.diversity * 0.20 +
            metrics.coverage * 0.20 +
            metrics.balance * 0.10
        )
        
        # Penalties for issues and warnings
        issue_penalty = min(num_issues * 0.1, 0.5)  # Max 50% penalty
        warning_penalty = min(num_warnings * 0.02, 0.2)  # Max 20% penalty
        
        final_score = max(0.0, base_score - issue_penalty - warning_penalty)
        
        return final_score
    
    def _save_validation_report(self, result: ValidationResult, contracts: List[ContractInfo]):
        """Save detailed validation report"""
        
        report_dir = self.output_dir / result.dataset_name
        report_dir.mkdir(parents=True, exist_ok=True)
        
        # Save main validation result
        result_file = report_dir / "validation_result.json"
        result_data = {
            "dataset_name": result.dataset_name,
            "validation_date": datetime.now().isoformat(),
            "is_valid": result.is_valid,
            "validation_score": result.validation_score,
            "issues": result.issues,
            "warnings": result.warnings,
            "statistics": result.statistics
        }
        
        with open(result_file, 'w') as f:
            json.dump(result_data, f, indent=2)
        
        # Generate detailed statistics
        detailed_stats = self._generate_detailed_statistics(contracts)
        stats_file = report_dir / "detailed_statistics.json"
        with open(stats_file, 'w') as f:
            json.dump(detailed_stats, f, indent=2, default=str)
        
        # Generate visualization plots
        self._generate_validation_plots(contracts, report_dir)
        
        self.logger.info(f"Validation report saved to {report_dir}")
    
    def _generate_detailed_statistics(self, contracts: List[ContractInfo]) -> Dict[str, Any]:
        """Generate detailed statistical analysis"""
        
        # Basic statistics
        stats = {
            "contract_count": len(contracts),
            "projects": len(set(c.project_name for c in contracts)),
            "source_datasets": list(set(c.source_dataset for c in contracts))
        }
        
        # Lines of code statistics
        loc_values = [c.lines_of_code for c in contracts]
        stats["lines_of_code"] = {
            "mean": float(np.mean(loc_values)),
            "median": float(np.median(loc_values)),
            "std": float(np.std(loc_values)),
            "min": int(np.min(loc_values)),
            "max": int(np.max(loc_values)),
            "percentiles": {
                "25th": float(np.percentile(loc_values, 25)),
                "75th": float(np.percentile(loc_values, 75)),
                "90th": float(np.percentile(loc_values, 90)),
                "95th": float(np.percentile(loc_values, 95))
            }
        }
        
        # Vulnerability statistics
        vuln_counts = {}
        severity_counts = {}
        
        for contract in contracts:
            for vuln in contract.vulnerability_labels:
                vuln_counts[vuln] = vuln_counts.get(vuln, 0) + 1
            for severity in contract.severity_levels:
                severity_counts[severity] = severity_counts.get(severity, 0) + 1
        
        stats["vulnerabilities"] = {
            "total_vulnerabilities": sum(vuln_counts.values()),
            "unique_types": len(vuln_counts),
            "type_distribution": vuln_counts,
            "severity_distribution": severity_counts,
            "avg_per_contract": sum(vuln_counts.values()) / len(contracts) if contracts else 0
        }
        
        # Contract classification
        contracts_with_vulns = sum(1 for c in contracts if c.vulnerability_labels)
        stats["classification"] = {
            "vulnerable_contracts": contracts_with_vulns,
            "clean_contracts": len(contracts) - contracts_with_vulns,
            "vulnerability_rate": contracts_with_vulns / len(contracts) if contracts else 0
        }
        
        return stats
    
    def _generate_validation_plots(self, contracts: List[ContractInfo], output_dir: Path):
        """Generate validation visualization plots"""
        
        try:
            import matplotlib.pyplot as plt
            import seaborn as sns
            
            # Set style
            plt.style.use('default')
            sns.set_palette("husl")
            
            # 1. Lines of code distribution
            fig, ((ax1, ax2), (ax3, ax4)) = plt.subplots(2, 2, figsize=(15, 12))
            
            loc_values = [c.lines_of_code for c in contracts]
            
            ax1.hist(loc_values, bins=30, alpha=0.7, edgecolor='black')
            ax1.set_xlabel('Lines of Code')
            ax1.set_ylabel('Frequency')
            ax1.set_title('Distribution of Contract Sizes')
            
            # 2. Vulnerability type distribution
            vuln_counts = {}
            for contract in contracts:
                for vuln in contract.vulnerability_labels:
                    vuln_counts[vuln] = vuln_counts.get(vuln, 0) + 1
            
            if vuln_counts:
                vuln_types = list(vuln_counts.keys())
                vuln_values = list(vuln_counts.values())
                
                ax2.bar(range(len(vuln_types)), vuln_values)
                ax2.set_xlabel('Vulnerability Types')
                ax2.set_ylabel('Count')
                ax2.set_title('Vulnerability Type Distribution')
                ax2.set_xticks(range(len(vuln_types)))
                ax2.set_xticklabels(vuln_types, rotation=45, ha='right')
            
            # 3. Severity distribution
            severity_counts = {}
            for contract in contracts:
                for severity in contract.severity_levels:
                    severity_counts[severity] = severity_counts.get(severity, 0) + 1
            
            if severity_counts:
                ax3.pie(severity_counts.values(), labels=severity_counts.keys(), autopct='%1.1f%%')
                ax3.set_title('Severity Level Distribution')
            
            # 4. Contracts by project
            project_counts = {}
            for contract in contracts:
                project_counts[contract.project_name] = project_counts.get(contract.project_name, 0) + 1
            
            if len(project_counts) <= 20:  # Only show if reasonable number of projects
                ax4.bar(range(len(project_counts)), list(project_counts.values()))
                ax4.set_xlabel('Projects')
                ax4.set_ylabel('Contract Count')
                ax4.set_title('Contracts per Project')
                ax4.set_xticks(range(len(project_counts)))
                ax4.set_xticklabels(list(project_counts.keys()), rotation=45, ha='right')
            else:
                # Show distribution instead
                counts_values = list(project_counts.values())
                ax4.hist(counts_values, bins=min(20, len(counts_values)), alpha=0.7)
                ax4.set_xlabel('Contracts per Project')
                ax4.set_ylabel('Number of Projects')
                ax4.set_title('Distribution of Project Sizes')
            
            plt.tight_layout()
            plt.savefig(output_dir / "validation_plots.png", dpi=300, bbox_inches='tight')
            plt.close()
            
            self.logger.info(f"Validation plots saved to {output_dir}")
            
        except ImportError:
            self.logger.warning("Matplotlib not available, skipping plot generation")
        except Exception as e:
            self.logger.warning(f"Failed to generate plots: {e}")
    
    def validate_all_datasets(self) -> Dict[str, ValidationResult]:
        """Validate all available datasets"""
        
        self.logger.info("Starting validation of all datasets...")
        
        # Find all processed datasets
        processed_dir = self.data_dir / "processed"
        
        if not processed_dir.exists():
            self.logger.error("No processed datasets found")
            return {}
        
        dataset_dirs = [d for d in processed_dir.iterdir() if d.is_dir()]
        
        results = {}
        
        for dataset_dir in dataset_dirs:
            dataset_name = dataset_dir.name
            
            try:
                result = self.validate_dataset(dataset_name)
                results[dataset_name] = result
                
                # Log validation summary
                status = "✓ VALID" if result.is_valid else "✗ INVALID"
                self.logger.info(f"{dataset_name}: {status} (score: {result.validation_score:.3f})")
                
            except Exception as e:
                self.logger.error(f"Validation failed for {dataset_name}: {e}")
                continue
        
        # Generate overall validation summary
        self._generate_validation_summary(results)
        
        return results
    
    def _generate_validation_summary(self, results: Dict[str, ValidationResult]):
        """Generate overall validation summary"""
        
        summary = {
            "validation_date": datetime.now().isoformat(),
            "datasets_validated": len(results),
            "datasets_valid": sum(1 for r in results.values() if r.is_valid),
            "average_score": np.mean([r.validation_score for r in results.values()]) if results else 0,
            "dataset_results": {
                name: {
                    "is_valid": result.is_valid,
                    "score": result.validation_score,
                    "issues": len(result.issues),
                    "warnings": len(result.warnings)
                }
                for name, result in results.items()
            }
        }
        
        summary_file = self.output_dir / "validation_summary.json"
        with open(summary_file, 'w') as f:
            json.dump(summary, f, indent=2)
        
        self.logger.info(f"Validation summary saved to {summary_file}")
        
        # Print summary
        print("\nDataset Validation Summary:")
        print("=" * 50)
        for name, result in results.items():
            status = "✓" if result.is_valid else "✗"
            print(f"{status} {name}: {result.validation_score:.3f} ({len(result.issues)} issues, {len(result.warnings)} warnings)")

# Example usage
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    validator = DatasetValidator(data_dir="./datasets")
    
    # Validate all datasets
    results = validator.validate_all_datasets()
    
    print(f"\nValidated {len(results)} datasets")
    valid_datasets = [name for name, result in results.items() if result.is_valid]
    print(f"Valid datasets: {valid_datasets}")