#!/usr/bin/env python3
"""
RL-Augmented Smart Contract Audit System
=======================================

Main entry point for running smart contract audits with reinforcement learning
policies for adaptive mode selection and dynamic stopping criteria.

This script replaces the original run.py with RL-enhanced orchestration that:
1. Uses RL policy to select optimal audit mode (BA/TA/Hybrid) 
2. Uses RL policy to decide when to stop consensus rounds
3. Tracks cost-accuracy tradeoffs for continuous learning
4. Provides comprehensive evaluation and reporting

Usage:
    python run_rl_audit.py --contract contract.sol --mode rl
    python run_rl_audit.py --batch dataset/ --evaluate
    python run_rl_audit.py --train --dataset synthetic
"""

import argparse
import logging
import os
import sys
import json
from pathlib import Path
from typing import Dict, List, Optional

# Add project paths
sys.path.append(os.path.dirname(__file__))

from integration.rl_orchestrator import RLOrchestrator
from rl_policies.mode_selector import ModeSelector
from rl_policies.stopping_policy import StoppingPolicy
from rl_environment.contract_analyzer import ContractAnalyzer
from evaluation.benchmark_datasets import BenchmarkDatasetManager, ContractSample

class RLAuditCLI:
    """
    Command-line interface for RL-augmented smart contract auditing
    """
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        
        # Configuration
        self.config = {
            'models_dir': './models',
            'data_dir': './datasets', 
            'results_dir': './results',
            'groq_api_key': os.getenv('GROQ_API_KEY'),
        }
        
        # Ensure directories exist
        for dir_path in [self.config['models_dir'], self.config['data_dir'], self.config['results_dir']]:
            Path(dir_path).mkdir(parents=True, exist_ok=True)
    
    def setup_logging(self, verbose: bool = False):
        """Setup logging configuration"""
        
        level = logging.DEBUG if verbose else logging.INFO
        logging.basicConfig(
            level=level,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.StreamHandler(sys.stdout),
                logging.FileHandler(f"{self.config['results_dir']}/rl_audit.log")
            ]
        )
    
    def load_or_train_policies(self, force_train: bool = False, dataset_name: str = "synthetic"):
        """Load existing policies or train new ones"""
        
        self.logger.info("Setting up RL policies...")
        
        # Prepare dataset
        dataset_manager = BenchmarkDatasetManager(data_dir=self.config['data_dir'])
        
        if dataset_name == "synthetic":
            dataset_split = dataset_manager.generate_synthetic_dataset(n_contracts=1000)
            self.logger.info("Generated synthetic dataset with 1000 contracts")
        else:
            # Try to load real datasets
            datasets = dataset_manager.download_and_prepare_datasets()
            if dataset_name in datasets:
                dataset_split = datasets[dataset_name]
                self.logger.info(f"Loaded {dataset_name} dataset")
            else:
                self.logger.warning(f"Dataset {dataset_name} not available, using synthetic")
                dataset_split = dataset_manager.generate_synthetic_dataset(n_contracts=1000)
        
        # Initialize policies
        mode_selector = ModeSelector(
            dataset_split=dataset_split,
            model_save_path=f"{self.config['models_dir']}/mode_selector"
        )
        
        stopping_policy = StoppingPolicy(
            dataset_split=dataset_split,
            model_save_path=f"{self.config['models_dir']}/stopping_policy"
        )
        
        # Train or load policies
        mode_selector_path = f"{self.config['models_dir']}/mode_selector/mode_selector.zip"
        stopping_policy_path = f"{self.config['models_dir']}/stopping_policy/stopping_policy.zip"
        
        if force_train or not (os.path.exists(mode_selector_path) and os.path.exists(stopping_policy_path)):
            self.logger.info("Training RL policies...")
            
            # Train mode selector
            self.logger.info("Training Mode Selector Policy...")
            mode_train_summary = mode_selector.train(total_timesteps=50000)
            self.logger.info(f"Mode Selector training completed: {mode_train_summary}")
            
            # Train stopping policy  
            self.logger.info("Training Stopping Policy...")
            stop_train_summary = stopping_policy.train(total_timesteps=50000)
            self.logger.info(f"Stopping Policy training completed: {stop_train_summary}")
            
        else:
            self.logger.info("Loading pre-trained RL policies...")
            mode_selector.load_policy(mode_selector_path)
            stopping_policy.load_policy(stopping_policy_path)
        
        return mode_selector, stopping_policy, dataset_split
    
    def run_single_audit(self, contract_path: str, project_name: str = None):
        """Run audit on single contract"""
        
        self.logger.info(f"Running RL audit on: {contract_path}")
        
        # Load contract
        with open(contract_path, 'r', encoding='utf-8') as f:
            contract_code = f.read()
        
        project_name = project_name or Path(contract_path).stem
        
        # Setup RL components
        mode_selector, stopping_policy, _ = self.load_or_train_policies()
        
        contract_analyzer = ContractAnalyzer()
        
        # Create orchestrator
        orchestrator = RLOrchestrator(
            mode_selector=mode_selector,
            stopping_policy=stopping_policy,
            contract_analyzer=contract_analyzer,
            groq_api_key=self.config['groq_api_key']
        )
        
        # Run audit
        audit_session = orchestrator.audit_contract(
            contract_code=contract_code,
            project_name=project_name
        )
        
        # Save results
        results_path = f"{self.config['results_dir']}/{project_name}_audit.json"
        self._save_audit_results(audit_session, results_path)
        
        # Print summary
        self._print_audit_summary(audit_session)
        
        return audit_session
    
    def run_batch_audit(self, dataset_path: str, max_contracts: int = None):
        """Run batch audit on dataset"""
        
        self.logger.info(f"Running batch RL audit on: {dataset_path}")
        
        # Load dataset
        if os.path.isdir(dataset_path):
            # Directory of contract files
            contract_files = list(Path(dataset_path).glob("*.sol"))
            
            contracts = []
            for contract_file in contract_files:
                with open(contract_file, 'r', encoding='utf-8') as f:
                    contract_code = f.read()
                
                # Create contract sample
                sample = ContractSample(
                    contract_id=contract_file.stem,
                    contract_name=contract_file.stem,
                    source_code=contract_code,
                    features=None,  # Will be extracted during audit
                    vulnerabilities=[],  # No ground truth
                    dataset_source="local"
                )
                contracts.append(sample)
                
        else:
            self.logger.error(f"Invalid dataset path: {dataset_path}")
            return []
        
        # Limit contracts if specified
        if max_contracts:
            contracts = contracts[:max_contracts]
        
        # Setup RL components
        mode_selector, stopping_policy, _ = self.load_or_train_policies()
        
        contract_analyzer = ContractAnalyzer()
        
        # Create orchestrator
        orchestrator = RLOrchestrator(
            mode_selector=mode_selector,
            stopping_policy=stopping_policy,
            contract_analyzer=contract_analyzer,
            groq_api_key=self.config['groq_api_key']
        )
        
        # Run batch audit
        audit_sessions = orchestrator.batch_audit(contracts, max_contracts)
        
        # Save batch results
        batch_results_path = f"{self.config['results_dir']}/batch_audit_results.json"
        self._save_batch_results(audit_sessions, batch_results_path)
        
        # Print batch summary
        self._print_batch_summary(audit_sessions, orchestrator)
        
        return audit_sessions
    
    def evaluate_policies(self, dataset_name: str = "synthetic"):
        """Evaluate RL policies performance"""
        
        self.logger.info("Evaluating RL policies...")
        
        # Load policies and dataset
        mode_selector, stopping_policy, dataset_split = self.load_or_train_policies(dataset_name=dataset_name)
        
        # Evaluate mode selector
        self.logger.info("Evaluating Mode Selector Policy...")
        mode_eval_results = mode_selector.evaluate(
            test_samples=dataset_split.test_samples,
            n_episodes=100
        )
        
        # Evaluate stopping policy
        self.logger.info("Evaluating Stopping Policy...")  
        stop_eval_results = stopping_policy.evaluate(
            test_samples=dataset_split.test_samples,
            n_episodes=100
        )
        
        # Combined evaluation results
        evaluation_results = {
            'mode_selector_evaluation': mode_eval_results,
            'stopping_policy_evaluation': stop_eval_results,
            'dataset_info': {
                'dataset_name': dataset_name,
                'test_samples': len(dataset_split.test_samples)
            }
        }
        
        # Save evaluation results
        eval_results_path = f"{self.config['results_dir']}/policy_evaluation.json"
        with open(eval_results_path, 'w') as f:
            json.dump(evaluation_results, f, indent=2, default=str)
        
        # Print evaluation summary
        self._print_evaluation_summary(evaluation_results)
        
        return evaluation_results
    
    def _save_audit_results(self, audit_session, results_path: str):
        """Save audit session results to file"""
        
        # Convert to serializable format
        results_dict = {
            'session_id': audit_session.session_id,
            'contract_features': audit_session.contract_features.__dict__ if audit_session.contract_features else {},
            'selected_mode': audit_session.selected_mode.value if audit_session.selected_mode else None,
            'rl_decisions': [
                {
                    'decision_type': d.decision_type,
                    'timestamp': d.timestamp,
                    'action_taken': d.action_taken,
                    'confidence': d.confidence,
                } for d in audit_session.rl_decisions
            ],
            'consensus_rounds': audit_session.consensus_rounds,
            'final_results': audit_session.final_results,
            'performance_metrics': audit_session.performance_metrics,
        }
        
        with open(results_path, 'w') as f:
            json.dump(results_dict, f, indent=2, default=str)
            
        self.logger.info(f"Audit results saved to: {results_path}")
    
    def _save_batch_results(self, audit_sessions: List, batch_results_path: str):
        """Save batch audit results"""
        
        batch_results = {
            'total_audits': len(audit_sessions),
            'timestamp': os.path.getctime,
            'individual_results': [
                {
                    'session_id': session.session_id,
                    'selected_mode': session.selected_mode.value if session.selected_mode else None,
                    'cost': session.final_results.get('estimated_cost', 0),
                    'vulnerabilities': session.final_results.get('vulnerabilities_count', 0),
                    'confidence': session.final_results.get('avg_confidence', 0),
                    'consensus_rounds': session.final_results.get('consensus_rounds', 0),
                } for session in audit_sessions
            ]
        }
        
        with open(batch_results_path, 'w') as f:
            json.dump(batch_results, f, indent=2, default=str)
            
        self.logger.info(f"Batch results saved to: {batch_results_path}")
    
    def _print_audit_summary(self, audit_session):
        """Print summary of single audit"""
        
        print("\n" + "="*60)
        print(f"RL AUDIT SUMMARY - {audit_session.session_id}")
        print("="*60)
        
        if audit_session.selected_mode:
            print(f"Selected Mode: {audit_session.selected_mode.value}")
            
        results = audit_session.final_results
        print(f"Estimated Cost: ${results.get('estimated_cost', 0):.2f}")
        print(f"Execution Time: {results.get('execution_time', 0):.1f}s")
        print(f"Vulnerabilities Found: {results.get('vulnerabilities_count', 0)}")
        print(f"Average Confidence: {results.get('avg_confidence', 0):.3f}")
        print(f"Consensus Rounds: {results.get('consensus_rounds', 0)}")
        
        # RL decisions summary
        rl_decisions = audit_session.rl_decisions
        mode_decisions = [d for d in rl_decisions if d.decision_type == "mode_selection"]
        stop_decisions = [d for d in rl_decisions if d.decision_type == "stopping"]
        
        print(f"\nRL Decisions:")
        print(f"  Mode Selection: {len(mode_decisions)} decision(s)")
        print(f"  Stopping: {len(stop_decisions)} decision(s)")
        
        if audit_session.performance_metrics:
            perf = audit_session.performance_metrics
            print(f"\nPerformance Metrics:")
            print(f"  Cost Efficiency: {perf.get('cost_efficiency', 0):.3f}")
            if 'f1_score' in perf:
                print(f"  F1 Score: {perf.get('f1_score', 0):.3f}")
                print(f"  Precision: {perf.get('precision', 0):.3f}")
                print(f"  Recall: {perf.get('recall', 0):.3f}")
        
        print("="*60)
    
    def _print_batch_summary(self, audit_sessions: List, orchestrator: RLOrchestrator):
        """Print summary of batch audit"""
        
        aggregate_metrics = orchestrator.get_aggregate_metrics(audit_sessions)
        
        print("\n" + "="*60)
        print(f"BATCH RL AUDIT SUMMARY")
        print("="*60)
        
        print(f"Total Audits: {aggregate_metrics.get('total_sessions', 0)}")
        print(f"Total Cost: ${aggregate_metrics.get('total_cost', 0):.2f}")
        print(f"Average Cost per Audit: ${aggregate_metrics.get('avg_cost_per_audit', 0):.2f}")
        print(f"Average Consensus Rounds: {aggregate_metrics.get('avg_consensus_rounds', 0):.1f}")
        print(f"Average Confidence: {aggregate_metrics.get('avg_confidence', 0):.3f}")
        print(f"Cost Efficiency: {aggregate_metrics.get('avg_cost_efficiency', 0):.3f}")
        
        # Mode distribution
        mode_dist = aggregate_metrics.get('mode_distribution', {})
        print(f"\nMode Selection Distribution:")
        for mode, count in mode_dist.items():
            pct = count / aggregate_metrics.get('total_sessions', 1) * 100
            print(f"  {mode}: {count} ({pct:.1f}%)")
        
        print("="*60)
    
    def _print_evaluation_summary(self, evaluation_results):
        """Print evaluation results summary"""
        
        print("\n" + "="*60)
        print("RL POLICIES EVALUATION SUMMARY")
        print("="*60)
        
        mode_eval = evaluation_results['mode_selector_evaluation']
        stop_eval = evaluation_results['stopping_policy_evaluation']
        
        print("Mode Selector Policy:")
        print(f"  Mean Reward: {mode_eval.get('mean_reward', 0):.3f}")
        print(f"  Mean Efficiency: {mode_eval.get('mean_efficiency', 0):.3f}")
        
        mode_dist = mode_eval.get('mode_distribution', {})
        print(f"  Mode Distribution:")
        for mode, pct in mode_dist.items():
            print(f"    {mode}: {pct:.1f}%")
        
        print(f"\nStopping Policy:")
        print(f"  Mean Stopping Rounds: {stop_eval.get('mean_stopping_rounds', 0):.1f}")
        print(f"  Mean Final Confidence: {stop_eval.get('mean_final_confidence', 0):.3f}")
        print(f"  Cost Efficiency: {stop_eval.get('cost_efficiency', 0):.3f}")
        
        print("="*60)

def main():
    """Main CLI entry point"""
    
    parser = argparse.ArgumentParser(description="RL-Augmented Smart Contract Audit System")
    
    # Main operation modes
    parser.add_argument('--contract', type=str, help='Path to single contract file (.sol)')
    parser.add_argument('--batch', type=str, help='Path to directory of contract files')
    parser.add_argument('--train', action='store_true', help='Force training of RL policies')
    parser.add_argument('--evaluate', action='store_true', help='Evaluate RL policies performance')
    
    # Configuration options
    parser.add_argument('--dataset', type=str, default='synthetic', 
                       help='Dataset for training/evaluation (synthetic, smartbugs, etc.)')
    parser.add_argument('--max-contracts', type=int, help='Maximum contracts to process in batch mode')
    parser.add_argument('--project-name', type=str, help='Project name for single contract audit')
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose logging')
    parser.add_argument('--groq-api-key', type=str, help='Groq API key (or set GROQ_API_KEY env var)')
    
    args = parser.parse_args()
    
    # Initialize CLI
    cli = RLAuditCLI()
    cli.setup_logging(args.verbose)
    
    # Set API key if provided
    if args.groq_api_key:
        cli.config['groq_api_key'] = args.groq_api_key
    
    try:
        if args.train:
            # Train RL policies
            cli.load_or_train_policies(force_train=True, dataset_name=args.dataset)
            
        elif args.evaluate:
            # Evaluate policies
            cli.evaluate_policies(dataset_name=args.dataset)
            
        elif args.contract:
            # Single contract audit
            if not os.path.exists(args.contract):
                print(f"Error: Contract file not found: {args.contract}")
                sys.exit(1)
                
            cli.run_single_audit(args.contract, args.project_name)
            
        elif args.batch:
            # Batch audit
            if not os.path.exists(args.batch):
                print(f"Error: Batch directory not found: {args.batch}")
                sys.exit(1)
                
            cli.run_batch_audit(args.batch, args.max_contracts)
            
        else:
            # No operation specified
            parser.print_help()
            print("\nExample usage:")
            print("  python run_rl_audit.py --contract sample.sol")
            print("  python run_rl_audit.py --batch contracts/ --max-contracts 10")
            print("  python run_rl_audit.py --train --dataset synthetic")
            print("  python run_rl_audit.py --evaluate")
            
    except KeyboardInterrupt:
        print("\nAudit interrupted by user")
        sys.exit(1)
    except Exception as e:
        cli.logger.error(f"Audit failed: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()