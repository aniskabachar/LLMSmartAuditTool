"""
Baseline Experiments for RL-Augmented Smart Contract Auditing

This module runs baseline experiments with fixed BA/TA/Hybrid modes to establish
performance benchmarks before RL training.
"""

import asyncio
import logging
import json
import time
from typing import Dict, List, Any, Optional
from pathlib import Path
import pandas as pd
import numpy as np
from dataclasses import dataclass, asdict

from evaluation.cost_accuracy_framework import CostAccuracyEvaluator, EvaluationConfig
from rl_environment.reward_function import VulnerabilityDetection, VulnerabilityType
from datasets.dataset_validation import DatasetValidator
from infrastructure.groq_backend import GroqBackend

logger = logging.getLogger(__name__)

@dataclass
class BaselineConfig:
    """Configuration for baseline experiments"""
    dataset_path: str
    output_dir: str
    modes: List[str] = None
    sample_size: int = 50  # Number of contracts to evaluate
    repetitions: int = 3   # Repetitions per contract for statistical stability
    timeout: int = 300     # Timeout per audit in seconds
    
    def __post_init__(self):
        if self.modes is None:
            self.modes = ['BA', 'TA', 'Hybrid']

class BaselineExperimentRunner:
    """Runs comprehensive baseline experiments"""
    
    def __init__(self, config: BaselineConfig):
        self.config = config
        self.output_path = Path(config.output_dir)
        self.output_path.mkdir(parents=True, exist_ok=True)
        
        # Initialize components
        self.dataset_validator = DatasetValidator()
        self.groq_backend = GroqBackend()
        
        # Results storage
        self.baseline_results = {}
        
    async def run_all_baselines(self) -> Dict[str, Any]:
        """Run complete baseline evaluation"""
        
        logger.info("Starting comprehensive baseline experiments")
        start_time = time.time()
        
        # Load and validate dataset
        contracts, ground_truth = await self._prepare_dataset()
        
        if not contracts:
            raise ValueError("No valid contracts found in dataset")
        
        logger.info(f"Loaded {len(contracts)} contracts for baseline evaluation")
        
        # Run baseline experiments for each mode
        baseline_results = {}
        
        for mode in self.config.modes:
            logger.info(f"Running baseline experiments for {mode} mode")
            
            mode_results = await self._run_mode_baseline(
                mode, contracts, ground_truth
            )
            
            baseline_results[mode] = mode_results
        
        # Generate comparative analysis
        comparative_analysis = self._analyze_baseline_results(baseline_results)
        
        # Create comprehensive report
        final_report = {
            'config': asdict(self.config),
            'execution_time': time.time() - start_time,
            'dataset_info': {
                'total_contracts': len(contracts),
                'contracts_evaluated': self.config.sample_size,
                'repetitions_per_contract': self.config.repetitions
            },
            'baseline_results': baseline_results,
            'comparative_analysis': comparative_analysis,
            'statistical_summary': self._generate_statistical_summary(baseline_results),
            'recommendations': self._generate_recommendations(comparative_analysis)
        }
        
        # Save results
        await self._save_baseline_results(final_report)
        
        logger.info(f"Baseline experiments completed in {final_report['execution_time']:.2f} seconds")
        
        return final_report
    
    async def _prepare_dataset(self) -> tuple[List[str], Dict[str, List[VulnerabilityDetection]]]:
        """Load and prepare dataset for baseline experiments"""
        
        # Load contracts from dataset
        dataset_path = Path(self.config.dataset_path)
        
        if not dataset_path.exists():
            logger.warning(f"Dataset path {dataset_path} not found, creating mock dataset")
            return self._create_mock_dataset()
        
        # Find contract files
        contract_files = list(dataset_path.glob("**/*.sol"))
        
        if len(contract_files) == 0:
            logger.warning("No .sol files found, creating mock dataset")
            return self._create_mock_dataset()
        
        # Sample contracts for evaluation
        if len(contract_files) > self.config.sample_size:
            np.random.seed(42)  # For reproducibility
            contract_files = np.random.choice(
                contract_files, 
                size=self.config.sample_size, 
                replace=False
            ).tolist()
        
        contracts = [str(f) for f in contract_files]
        
        # Load or generate ground truth
        ground_truth = {}
        for contract in contracts:
            ground_truth[contract] = await self._load_ground_truth(contract)
        
        return contracts, ground_truth
    
    def _create_mock_dataset(self) -> tuple[List[str], Dict[str, List[VulnerabilityDetection]]]:
        """Create mock dataset for testing"""
        
        logger.info("Creating mock dataset for baseline experiments")
        
        contracts = [f"mock_contract_{i}.sol" for i in range(self.config.sample_size)]
        ground_truth = {}
        
        np.random.seed(42)  # For reproducibility
        
        for contract in contracts:
            # Generate random vulnerabilities
            num_vulns = np.random.poisson(2)  # Average 2 vulnerabilities
            vulns = []
            
            for j in range(num_vulns):
                vuln_type = np.random.choice(list(VulnerabilityType))
                confidence = np.random.uniform(0.8, 1.0)
                
                vulns.append(VulnerabilityDetection(
                    vuln_type=vuln_type,
                    confidence=confidence,
                    location=f"{contract}:{np.random.randint(10, 100)}",
                    description=f"Mock {vuln_type.value} vulnerability",
                    is_true_positive=True
                ))
            
            ground_truth[contract] = vulns
        
        return contracts, ground_truth
    
    async def _load_ground_truth(self, contract_path: str) -> List[VulnerabilityDetection]:
        """Load ground truth vulnerabilities for a contract"""
        
        # Try to load from annotations file
        annotations_file = Path(contract_path).with_suffix('.json')
        
        if annotations_file.exists():
            try:
                with open(annotations_file, 'r') as f:
                    data = json.load(f)
                
                vulnerabilities = []
                for vuln_data in data.get('vulnerabilities', []):
                    vulnerabilities.append(VulnerabilityDetection(
                        vuln_type=VulnerabilityType(vuln_data['type']),
                        confidence=vuln_data.get('confidence', 1.0),
                        location=vuln_data['location'],
                        description=vuln_data['description'],
                        is_true_positive=True
                    ))
                
                return vulnerabilities
                
            except Exception as e:
                logger.warning(f"Failed to load annotations for {contract_path}: {e}")
        
        # Fallback to mock vulnerabilities
        return self._generate_mock_vulnerabilities(contract_path)
    
    def _generate_mock_vulnerabilities(self, contract_path: str) -> List[VulnerabilityDetection]:
        """Generate mock vulnerabilities for a contract"""
        
        # Use contract path hash for consistent randomness
        np.random.seed(hash(contract_path) % (2**32))
        
        num_vulns = np.random.poisson(1.5)  # Average 1.5 vulnerabilities
        vulnerabilities = []
        
        for i in range(num_vulns):
            vuln_type = np.random.choice(list(VulnerabilityType))
            
            vulnerabilities.append(VulnerabilityDetection(
                vuln_type=vuln_type,
                confidence=1.0,
                location=f"{contract_path}:{20 + i * 10}",
                description=f"Mock {vuln_type.value} vulnerability",
                is_true_positive=True
            ))
        
        return vulnerabilities
    
    async def _run_mode_baseline(
        self,
        mode: str,
        contracts: List[str],
        ground_truth: Dict[str, List[VulnerabilityDetection]]
    ) -> Dict[str, Any]:
        """Run baseline experiments for a specific mode"""
        
        # Create evaluation configuration
        eval_config = EvaluationConfig(
            dataset_path=self.config.dataset_path,
            output_dir=str(self.output_path / mode),
            modes_to_evaluate=[mode],
            num_parallel_jobs=2,  # Conservative for baseline
            timeout_per_audit=self.config.timeout
        )
        
        evaluator = CostAccuracyEvaluator(eval_config)
        
        # Run multiple repetitions for statistical stability
        all_repetition_results = []
        
        for rep in range(self.config.repetitions):
            logger.info(f"Running {mode} baseline repetition {rep + 1}/{self.config.repetitions}")
            
            # Run evaluation
            rep_results = evaluator.run_comprehensive_evaluation(contracts, ground_truth)
            all_repetition_results.append(rep_results)
        
        # Aggregate results across repetitions
        aggregated_results = self._aggregate_repetition_results(all_repetition_results)
        
        return {
            'mode': mode,
            'repetitions': self.config.repetitions,
            'individual_repetitions': all_repetition_results,
            'aggregated_metrics': aggregated_results,
            'stability_analysis': self._analyze_stability(all_repetition_results)
        }
    
    def _aggregate_repetition_results(self, repetition_results: List[Dict]) -> Dict[str, Any]:
        """Aggregate results across multiple repetitions"""
        
        # Extract metrics from each repetition
        accuracy_scores = []
        cost_scores = []
        f1_scores = []
        precision_scores = []
        recall_scores = []
        
        for rep_result in repetition_results:
            comparison_metrics = list(rep_result['comparison_metrics'].values())[0]
            
            accuracy_scores.append(comparison_metrics['avg_accuracy'])
            cost_scores.append(comparison_metrics['avg_cost'])
            f1_scores.append(comparison_metrics['avg_f1_score'])
            precision_scores.append(comparison_metrics['avg_precision'])
            recall_scores.append(comparison_metrics['avg_recall'])
        
        # Calculate statistics
        aggregated = {
            'accuracy': {
                'mean': float(np.mean(accuracy_scores)),
                'std': float(np.std(accuracy_scores)),
                'min': float(np.min(accuracy_scores)),
                'max': float(np.max(accuracy_scores)),
                'median': float(np.median(accuracy_scores))
            },
            'cost': {
                'mean': float(np.mean(cost_scores)),
                'std': float(np.std(cost_scores)),
                'min': float(np.min(cost_scores)),
                'max': float(np.max(cost_scores)),
                'median': float(np.median(cost_scores))
            },
            'f1_score': {
                'mean': float(np.mean(f1_scores)),
                'std': float(np.std(f1_scores)),
                'min': float(np.min(f1_scores)),
                'max': float(np.max(f1_scores)),
                'median': float(np.median(f1_scores))
            },
            'precision': {
                'mean': float(np.mean(precision_scores)),
                'std': float(np.std(precision_scores))
            },
            'recall': {
                'mean': float(np.mean(recall_scores)),
                'std': float(np.std(recall_scores))
            }
        }
        
        return aggregated
    
    def _analyze_stability(self, repetition_results: List[Dict]) -> Dict[str, Any]:
        """Analyze stability of results across repetitions"""
        
        # Extract key metrics
        accuracy_scores = []
        cost_scores = []
        
        for rep_result in repetition_results:
            comparison_metrics = list(rep_result['comparison_metrics'].values())[0]
            accuracy_scores.append(comparison_metrics['avg_accuracy'])
            cost_scores.append(comparison_metrics['avg_cost'])
        
        # Calculate coefficient of variation (CV)
        accuracy_cv = np.std(accuracy_scores) / np.mean(accuracy_scores) if np.mean(accuracy_scores) > 0 else 0
        cost_cv = np.std(cost_scores) / np.mean(cost_scores) if np.mean(cost_scores) > 0 else 0
        
        return {
            'accuracy_coefficient_of_variation': float(accuracy_cv),
            'cost_coefficient_of_variation': float(cost_cv),
            'accuracy_range': float(np.max(accuracy_scores) - np.min(accuracy_scores)),
            'cost_range': float(np.max(cost_scores) - np.min(cost_scores)),
            'stability_score': float(1.0 - (accuracy_cv + cost_cv) / 2.0)  # Higher is more stable
        }
    
    def _analyze_baseline_results(self, baseline_results: Dict[str, Any]) -> Dict[str, Any]:
        """Perform comparative analysis across baseline modes"""
        
        analysis = {
            'mode_rankings': {},
            'performance_gaps': {},
            'tradeoff_analysis': {},
            'statistical_significance': {}
        }
        
        # Extract aggregated metrics for each mode
        mode_metrics = {}
        for mode, results in baseline_results.items():
            mode_metrics[mode] = results['aggregated_metrics']
        
        # Rank modes by different criteria
        accuracy_ranking = sorted(
            mode_metrics.items(),
            key=lambda x: x[1]['accuracy']['mean'],
            reverse=True
        )
        
        cost_ranking = sorted(
            mode_metrics.items(),
            key=lambda x: x[1]['cost']['mean']
        )  # Lower cost is better
        
        efficiency_ranking = sorted(
            mode_metrics.items(),
            key=lambda x: x[1]['accuracy']['mean'] / x[1]['cost']['mean'],
            reverse=True
        )
        
        analysis['mode_rankings'] = {
            'by_accuracy': [mode for mode, _ in accuracy_ranking],
            'by_cost': [mode for mode, _ in cost_ranking],
            'by_efficiency': [mode for mode, _ in efficiency_ranking]
        }
        
        # Calculate performance gaps
        best_accuracy = accuracy_ranking[0][1]['accuracy']['mean']
        best_cost = cost_ranking[0][1]['cost']['mean']
        
        for mode, metrics in mode_metrics.items():
            accuracy_gap = best_accuracy - metrics['accuracy']['mean']
            cost_gap = metrics['cost']['mean'] - best_cost
            
            analysis['performance_gaps'][mode] = {
                'accuracy_gap': float(accuracy_gap),
                'cost_gap': float(cost_gap),
                'relative_accuracy_gap': float(accuracy_gap / best_accuracy) if best_accuracy > 0 else 0,
                'relative_cost_gap': float(cost_gap / best_cost) if best_cost > 0 else 0
            }
        
        # Tradeoff analysis
        analysis['tradeoff_analysis'] = self._analyze_tradeoffs(mode_metrics)
        
        return analysis
    
    def _analyze_tradeoffs(self, mode_metrics: Dict[str, Dict]) -> Dict[str, Any]:
        """Analyze cost-accuracy tradeoffs"""
        
        tradeoffs = {}
        
        for mode, metrics in mode_metrics.items():
            accuracy = metrics['accuracy']['mean']
            cost = metrics['cost']['mean']
            
            # Calculate efficiency score
            efficiency = accuracy / cost if cost > 0 else 0
            
            # Classify mode characteristics
            if accuracy >= 0.8 and cost <= 60:
                category = "high_performance"
            elif accuracy >= 0.7 and cost <= 50:
                category = "balanced"
            elif cost <= 40:
                category = "cost_effective"
            elif accuracy >= 0.85:
                category = "accuracy_focused"
            else:
                category = "standard"
            
            tradeoffs[mode] = {
                'efficiency_score': float(efficiency),
                'category': category,
                'accuracy_cost_ratio': float(accuracy / cost) if cost > 0 else 0,
                'is_pareto_optimal': False  # Will be determined in comparison
            }
        
        # Determine Pareto optimality
        modes = list(tradeoffs.keys())
        for mode in modes:
            mode_acc = mode_metrics[mode]['accuracy']['mean']
            mode_cost = mode_metrics[mode]['cost']['mean']
            
            is_dominated = False
            for other_mode in modes:
                if mode != other_mode:
                    other_acc = mode_metrics[other_mode]['accuracy']['mean']
                    other_cost = mode_metrics[other_mode]['cost']['mean']
                    
                    # Check if other mode dominates (higher accuracy AND lower cost)
                    if other_acc >= mode_acc and other_cost <= mode_cost:
                        if other_acc > mode_acc or other_cost < mode_cost:
                            is_dominated = True
                            break
            
            tradeoffs[mode]['is_pareto_optimal'] = not is_dominated
        
        return tradeoffs
    
    def _generate_statistical_summary(self, baseline_results: Dict[str, Any]) -> Dict[str, Any]:
        """Generate statistical summary of baseline results"""
        
        summary = {
            'overall_statistics': {},
            'mode_comparisons': {},
            'confidence_intervals': {}
        }
        
        # Overall statistics across all modes
        all_accuracy = []
        all_costs = []
        
        for mode, results in baseline_results.items():
            metrics = results['aggregated_metrics']
            all_accuracy.extend([metrics['accuracy']['mean']] * self.config.repetitions)
            all_costs.extend([metrics['cost']['mean']] * self.config.repetitions)
        
        summary['overall_statistics'] = {
            'total_experiments': len(baseline_results) * self.config.repetitions * self.config.sample_size,
            'accuracy_range': {
                'min': float(np.min(all_accuracy)),
                'max': float(np.max(all_accuracy)),
                'mean': float(np.mean(all_accuracy)),
                'std': float(np.std(all_accuracy))
            },
            'cost_range': {
                'min': float(np.min(all_costs)),
                'max': float(np.max(all_costs)),
                'mean': float(np.mean(all_costs)),
                'std': float(np.std(all_costs))
            }
        }
        
        return summary
    
    def _generate_recommendations(self, comparative_analysis: Dict[str, Any]) -> List[str]:
        """Generate recommendations based on baseline results"""
        
        recommendations = []
        
        # Accuracy recommendations
        best_accuracy_mode = comparative_analysis['mode_rankings']['by_accuracy'][0]
        recommendations.append(
            f"For maximum accuracy: Use {best_accuracy_mode} mode"
        )
        
        # Cost recommendations
        best_cost_mode = comparative_analysis['mode_rankings']['by_cost'][0]
        recommendations.append(
            f"For minimum cost: Use {best_cost_mode} mode"
        )
        
        # Efficiency recommendations
        best_efficiency_mode = comparative_analysis['mode_rankings']['by_efficiency'][0]
        recommendations.append(
            f"For best efficiency: Use {best_efficiency_mode} mode"
        )
        
        # Pareto optimal recommendations
        pareto_modes = [
            mode for mode, analysis in comparative_analysis['tradeoff_analysis'].items()
            if analysis['is_pareto_optimal']
        ]
        
        if pareto_modes:
            recommendations.append(
                f"Pareto optimal modes (no clear dominance): {', '.join(pareto_modes)}"
            )
        
        # RL potential recommendations
        performance_gaps = comparative_analysis['performance_gaps']
        max_accuracy_gap = max(gap['accuracy_gap'] for gap in performance_gaps.values())
        max_cost_gap = max(gap['cost_gap'] for gap in performance_gaps.values())
        
        if max_accuracy_gap > 0.1 or max_cost_gap > 10:
            recommendations.append(
                "Significant performance gaps detected - RL adaptation has high potential"
            )
        
        recommendations.append(
            "Consider RL-adaptive approach to dynamically select optimal mode based on contract characteristics"
        )
        
        return recommendations
    
    async def _save_baseline_results(self, results: Dict[str, Any]):
        """Save baseline experiment results"""
        
        # Save main results
        results_file = self.output_path / "baseline_results.json"
        with open(results_file, 'w') as f:
            json.dump(results, f, indent=2, default=str)
        
        # Save CSV summary
        self._save_baseline_csv(results)
        
        # Generate report
        await self._generate_baseline_report(results)
        
        logger.info(f"Baseline results saved to {self.output_path}")
    
    def _save_baseline_csv(self, results: Dict[str, Any]):
        """Save baseline results as CSV"""
        
        csv_data = []
        
        for mode, mode_results in results['baseline_results'].items():
            metrics = mode_results['aggregated_metrics']
            
            csv_data.append({
                'mode': mode,
                'accuracy_mean': metrics['accuracy']['mean'],
                'accuracy_std': metrics['accuracy']['std'],
                'cost_mean': metrics['cost']['mean'],
                'cost_std': metrics['cost']['std'],
                'f1_mean': metrics['f1_score']['mean'],
                'f1_std': metrics['f1_score']['std'],
                'stability_score': mode_results['stability_analysis']['stability_score']
            })
        
        if csv_data:
            df = pd.DataFrame(csv_data)
            csv_file = self.output_path / "baseline_summary.csv"
            df.to_csv(csv_file, index=False)
    
    async def _generate_baseline_report(self, results: Dict[str, Any]):
        """Generate human-readable baseline report"""
        
        report_lines = [
            "# Baseline Experiment Report",
            "",
            f"**Execution Time:** {results['execution_time']:.2f} seconds",
            f"**Contracts Evaluated:** {results['dataset_info']['contracts_evaluated']}",
            f"**Repetitions:** {results['dataset_info']['repetitions_per_contract']}",
            "",
            "## Mode Performance Summary",
            ""
        ]
        
        # Add performance summary
        for mode, mode_results in results['baseline_results'].items():
            metrics = mode_results['aggregated_metrics']
            stability = mode_results['stability_analysis']
            
            report_lines.extend([
                f"### {mode} Mode",
                f"- **Accuracy:** {metrics['accuracy']['mean']:.3f} ± {metrics['accuracy']['std']:.3f}",
                f"- **Cost:** {metrics['cost']['mean']:.1f} ± {metrics['cost']['std']:.1f}",
                f"- **F1 Score:** {metrics['f1_score']['mean']:.3f} ± {metrics['f1_score']['std']:.3f}",
                f"- **Stability Score:** {stability['stability_score']:.3f}",
                ""
            ])
        
        # Add recommendations
        report_lines.extend([
            "## Recommendations",
            ""
        ])
        
        for rec in results['recommendations']:
            report_lines.append(f"- {rec}")
        
        # Save report
        report_file = self.output_path / "baseline_report.md"
        with open(report_file, 'w') as f:
            f.write('\n'.join(report_lines))

async def run_baseline_experiments():
    """Run complete baseline experiments"""
    
    config = BaselineConfig(
        dataset_path="./datasets/contracts",
        output_dir="./evaluation/baseline_results",
        sample_size=20,  # Smaller for testing
        repetitions=3
    )
    
    runner = BaselineExperimentRunner(config)
    results = await runner.run_all_baselines()
    
    return results

if __name__ == "__main__":
    # Run baseline experiments
    asyncio.run(run_baseline_experiments())