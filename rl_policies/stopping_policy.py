"""
RL Stopping Policy Implementation  
=================================

This module implements the reinforcement learning policy that learns when to stop
consensus rounds in the multi-agent auditing process.

Key Goals:
1. Replace fixed cycleNum=3 with adaptive stopping criterion
2. Learn marginal accuracy gain vs additional cost tradeoffs
3. Stop early for simple contracts, continue longer for complex ones  
4. Optimize total cost-accuracy across different contract types

Architecture:
- State: Current audit progress + confidence metrics + contract features
- Action: Binary choice {CONTINUE, STOP}
- Reward: Marginal accuracy gain - additional cost penalty
- Policy: DQN with experience replay for temporal decision making
"""

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from typing import Dict, List, Tuple, Optional, Any, Deque
import gymnasium as gym
from dataclasses import dataclass
import json
import logging
from pathlib import Path
from collections import deque, namedtuple
import random

from stable_baselines3 import DQN
from stable_baselines3.common.policies import BasePolicy
from stable_baselines3.common.torch_layers import BaseFeaturesExtractor
from stable_baselines3.common.callbacks import BaseCallback
from stable_baselines3.common.monitor import Monitor
from stable_baselines3.common.buffers import ReplayBuffer

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from rl_environment.rl_architecture import ContractFeatures, AuditState
from evaluation.benchmark_datasets import ContractSample, DatasetSplit

@dataclass
class ConsensusRoundResult:
    """Results from a single consensus round"""
    round_number: int
    vulnerabilities_found: int
    confidence_score: float
    cost: float
    execution_time: float
    marginal_accuracy_gain: float
    
@dataclass
class StoppingReward:
    """Reward components for stopping decision"""
    accuracy_gain_reward: float
    cost_penalty: float
    convergence_bonus: float
    efficiency_penalty: float
    total_reward: float

class ConsensusStateEncoder(BaseFeaturesExtractor):
    """
    Neural network for encoding consensus state features
    
    Processes audit progress, confidence trends, and contract characteristics
    to determine optimal stopping points.
    """
    
    def __init__(self, observation_space: gym.Space, features_dim: int = 256):
        super().__init__(observation_space, features_dim)
        
        input_dim = observation_space.shape[0]  # 27 features
        
        # Separate encoders for different feature types
        self.contract_encoder = nn.Sequential(
            nn.Linear(22, 64),  # First 22 features are contract characteristics
            nn.ReLU(),
            nn.Dropout(0.1)
        )
        
        self.temporal_encoder = nn.Sequential(
            nn.Linear(5, 32),   # Last 5 features are temporal/progress
            nn.ReLU(),
            nn.Dropout(0.1)
        )
        
        # LSTM for modeling consensus progress sequence
        self.lstm_hidden_dim = 64
        self.consensus_lstm = nn.LSTM(
            input_size=32,
            hidden_size=self.lstm_hidden_dim,
            num_layers=2,
            dropout=0.1,
            batch_first=True
        )
        
        # Fusion network
        self.fusion_net = nn.Sequential(
            nn.Linear(64 + self.lstm_hidden_dim, 128),
            nn.ReLU(),
            nn.BatchNorm1d(128),
            nn.Dropout(0.2),
            
            nn.Linear(128, features_dim),
            nn.ReLU()
        )
        
    def forward(self, observations: torch.Tensor) -> torch.Tensor:
        batch_size = observations.shape[0]
        
        # Split features
        contract_features = observations[:, :22]
        temporal_features = observations[:, 22:]
        
        # Encode contract features (static)
        contract_encoded = self.contract_encoder(contract_features)
        
        # Encode temporal features
        temporal_encoded = self.temporal_encoder(temporal_features)
        
        # Process through LSTM (treating each sample as sequence length 1)
        temporal_expanded = temporal_encoded.unsqueeze(1)  # [batch, 1, 32]
        lstm_out, _ = self.consensus_lstm(temporal_expanded)
        lstm_features = lstm_out[:, -1, :]  # Take last output [batch, 64]
        
        # Fuse encodings
        fused_features = torch.cat([contract_encoded, lstm_features], dim=1)
        output = self.fusion_net(fused_features)
        
        return output

class StoppingQNetwork(nn.Module):
    """
    Q-network for stopping decisions
    
    Estimates Q-values for CONTINUE vs STOP actions based on current audit state.
    """
    
    def __init__(self, feature_dim: int = 256):
        super().__init__()
        
        # Q-value estimation network
        self.q_net = nn.Sequential(
            nn.Linear(feature_dim, 128),
            nn.ReLU(),
            nn.Dropout(0.1),
            
            nn.Linear(128, 64),
            nn.ReLU(),
            nn.Dropout(0.1),
            
            nn.Linear(64, 32),
            nn.ReLU(),
            
            nn.Linear(32, 2)  # Q-values for [CONTINUE, STOP]
        )
        
        # Dueling network architecture
        self.value_stream = nn.Sequential(
            nn.Linear(feature_dim, 128),
            nn.ReLU(),
            nn.Linear(128, 1)
        )
        
        self.advantage_stream = nn.Sequential(
            nn.Linear(feature_dim, 128),
            nn.ReLU(),
            nn.Linear(128, 2)
        )
        
    def forward(self, features: torch.Tensor) -> torch.Tensor:
        # Standard Q-network
        q_values = self.q_net(features)
        
        # Dueling architecture
        value = self.value_stream(features)
        advantage = self.advantage_stream(features)
        
        # Combine value and advantage
        q_dueling = value + advantage - advantage.mean(dim=1, keepdim=True)
        
        # Ensemble both approaches (can be weighted)
        return 0.7 * q_values + 0.3 * q_dueling

class ConsensusEnvironment(gym.Env):
    """
    Environment for training stopping policy
    
    Simulates multi-round consensus process with realistic accuracy 
    and cost progression.
    """
    
    def __init__(self,
                 dataset_samples: List[ContractSample],
                 max_rounds: int = 10,
                 base_round_cost: float = 0.05):
        
        super().__init__()
        
        self.dataset_samples = dataset_samples
        self.max_rounds = max_rounds
        self.base_round_cost = base_round_cost
        
        self.current_sample = None
        self.consensus_history = []
        self.current_round = 0
        
        # Define spaces
        self.observation_space = gym.spaces.Box(
            low=0.0, high=1.0, shape=(27,), dtype=np.float32
        )
        self.action_space = gym.spaces.Discrete(2)  # CONTINUE=0, STOP=1
        
        # Consensus simulation parameters
        self.convergence_rate = 0.15  # How quickly confidence converges
        self.diminishing_returns = 0.8  # Diminishing accuracy gains per round
        
    def reset(self, seed=None, options=None):
        """Reset for new consensus episode"""
        
        super().reset(seed=seed)
        
        # Select contract sample
        self.current_sample = random.choice(self.dataset_samples)
        
        # Initialize consensus state
        self.consensus_history = []
        self.current_round = 0
        
        # Simulate first round (automatic)
        initial_result = self._simulate_consensus_round(round_num=0)
        self.consensus_history.append(initial_result)
        self.current_round = 1
        
        # Create initial audit state
        audit_state = AuditState(
            contract_features=self.current_sample.features,
            current_round=self.current_round,
            max_rounds=self.max_rounds,
            cumulative_cost=initial_result.cost,
            vulnerabilities_found=initial_result.vulnerabilities_found,
            confidence_scores=[initial_result.confidence_score]
        )
        
        return audit_state.to_vector(), {}
    
    def step(self, action: int):
        """Execute stopping decision"""
        
        # action: 0=CONTINUE, 1=STOP
        
        if action == 1 or self.current_round >= self.max_rounds:
            # STOP decision or max rounds reached
            reward = self._calculate_stopping_reward(stopped=True)
            done = True
            
            info = {
                'action': 'STOP',
                'total_rounds': self.current_round,
                'final_confidence': self.consensus_history[-1].confidence_score,
                'total_cost': sum(r.cost for r in self.consensus_history),
                'total_vulnerabilities': self.consensus_history[-1].vulnerabilities_found
            }
            
        else:
            # CONTINUE decision - execute another round
            if self.current_round < self.max_rounds:
                round_result = self._simulate_consensus_round(self.current_round)
                self.consensus_history.append(round_result)
                self.current_round += 1
                
                reward = self._calculate_continuing_reward(round_result)
                done = False
                
                info = {
                    'action': 'CONTINUE',
                    'round_number': self.current_round,
                    'round_confidence': round_result.confidence_score,
                    'marginal_gain': round_result.marginal_accuracy_gain,
                    'round_cost': round_result.cost
                }
            else:
                # Forced stop at max rounds
                reward = self._calculate_stopping_reward(stopped=True, forced=True)
                done = True
                
                info = {
                    'action': 'FORCED_STOP',
                    'total_rounds': self.current_round,
                    'forced_stop': True
                }
        
        # Create next state
        audit_state = AuditState(
            contract_features=self.current_sample.features,
            current_round=self.current_round,
            max_rounds=self.max_rounds,
            cumulative_cost=sum(r.cost for r in self.consensus_history),
            vulnerabilities_found=self.consensus_history[-1].vulnerabilities_found,
            confidence_scores=[r.confidence_score for r in self.consensus_history]
        )
        
        return audit_state.to_vector(), reward, done, False, info
    
    def _simulate_consensus_round(self, round_num: int) -> ConsensusRoundResult:
        """Simulate a consensus round with realistic progression"""
        
        # Base accuracy starts high and has diminishing returns
        base_accuracy = 0.6 + 0.3 * (self.diminishing_returns ** round_num)
        
        # Contract complexity affects accuracy progression
        complexity_factor = self._get_complexity_factor()
        adjusted_accuracy = base_accuracy * complexity_factor
        
        # Confidence increases with rounds but converges
        confidence_score = 1.0 - (1.0 - adjusted_accuracy) * np.exp(-self.convergence_rate * (round_num + 1))
        confidence_score = min(0.99, confidence_score)  # Cap at 99%
        
        # Vulnerability detection (cumulative with some randomness)
        true_vulns = len(self.current_sample.vulnerabilities)
        if round_num == 0:
            # First round detects some vulnerabilities
            detected = max(1, int(adjusted_accuracy * true_vulns + np.random.normal(0, 0.5)))
        else:
            # Additional rounds may find more (with diminishing returns)
            prev_detected = self.consensus_history[-1].vulnerabilities_found
            additional = np.random.poisson(0.3 * (self.diminishing_returns ** round_num))
            detected = min(true_vulns + 2, prev_detected + additional)  # Allow some false positives
        
        # Calculate marginal accuracy gain
        if round_num == 0:
            marginal_gain = confidence_score
        else:
            prev_confidence = self.consensus_history[-1].confidence_score
            marginal_gain = max(0, confidence_score - prev_confidence)
        
        # Round cost (increases slightly with round number due to complexity)
        round_cost = self.base_round_cost * (1.1 ** round_num)
        
        return ConsensusRoundResult(
            round_number=round_num,
            vulnerabilities_found=detected,
            confidence_score=confidence_score,
            cost=round_cost,
            execution_time=5.0 + round_num * 2.0,  # Simulated execution time
            marginal_accuracy_gain=marginal_gain
        )
    
    def _get_complexity_factor(self) -> float:
        """Get complexity factor affecting accuracy progression"""
        
        features = self.current_sample.features
        
        # Calculate complexity score
        complexity = 0
        complexity += min(1.0, features.lines_of_code / 1000.0) * 0.3
        complexity += min(1.0, features.function_count / 30.0) * 0.2
        complexity += min(1.0, features.external_calls / 10.0) * 0.2
        complexity += min(1.0, features.conditional_count / 20.0) * 0.1
        complexity += features.uses_assembly * 0.1
        complexity += features.uses_delegatecall * 0.1
        
        # Convert to factor (complex contracts are harder to analyze accurately)
        return max(0.5, 1.2 - complexity)
    
    def _calculate_stopping_reward(self, stopped: bool, forced: bool = False) -> float:
        """Calculate reward for stopping consensus"""
        
        if not self.consensus_history:
            return -5.0  # Penalty for stopping with no rounds
        
        final_result = self.consensus_history[-1]
        total_cost = sum(r.cost for r in self.consensus_history)
        
        # Accuracy reward based on final confidence
        accuracy_reward = final_result.confidence_score * 10.0
        
        # Cost penalty
        cost_penalty = total_cost * 8.0
        
        # Efficiency bonus/penalty
        efficiency_bonus = 0.0
        
        if not forced:
            # Reward for stopping at good point
            if (final_result.confidence_score > 0.85 and 
                self.current_round <= 4):
                efficiency_bonus = 3.0  # Good stopping point
            elif (final_result.confidence_score > 0.75 and 
                  self.current_round <= 6):
                efficiency_bonus = 1.5  # Reasonable stopping point
            elif self.current_round > 7 and final_result.confidence_score < 0.9:
                efficiency_bonus = -2.0  # Should have stopped earlier
        else:
            # Penalty for hitting max rounds (poor stopping)
            efficiency_bonus = -1.0
        
        # Convergence bonus - reward for stopping when marginal gains are small
        if len(self.consensus_history) > 1:
            recent_gains = [r.marginal_accuracy_gain for r in self.consensus_history[-2:]]
            if all(gain < 0.05 for gain in recent_gains):
                efficiency_bonus += 2.0  # Good convergence detection
        
        return accuracy_reward - cost_penalty + efficiency_bonus
    
    def _calculate_continuing_reward(self, round_result: ConsensusRoundResult) -> float:
        """Calculate reward for continuing consensus"""
        
        # Reward based on marginal accuracy gain vs cost
        marginal_reward = round_result.marginal_accuracy_gain * 8.0
        cost_penalty = round_result.cost * 6.0
        
        # Bonus for meaningful progress
        progress_bonus = 0.0
        if round_result.marginal_accuracy_gain > 0.1:
            progress_bonus = 1.0  # Significant progress
        elif round_result.marginal_accuracy_gain > 0.05:
            progress_bonus = 0.5  # Some progress
        else:
            progress_bonus = -0.5  # Diminishing returns penalty
        
        return marginal_reward - cost_penalty + progress_bonus

class StoppingPolicyCallback(BaseCallback):
    """Callback for monitoring stopping policy training"""
    
    def __init__(self, eval_env: gym.Env, eval_freq: int = 1000, verbose: int = 1):
        super().__init__(verbose)
        
        self.eval_env = eval_env
        self.eval_freq = eval_freq
        
        # Tracking metrics
        self.stopping_stats = {
            'avg_rounds': [],
            'convergence_efficiency': [],
            'cost_efficiency': []
        }
        
    def _on_step(self) -> bool:
        """Called after each step"""
        
        infos = self.locals.get('infos', [])
        
        for info in infos:
            if 'total_rounds' in info:
                self.stopping_stats['avg_rounds'].append(info['total_rounds'])
                
            if 'total_cost' in info and 'final_confidence' in info:
                # Calculate efficiency metrics
                cost_efficiency = info['final_confidence'] / max(0.01, info['total_cost'])
                self.stopping_stats['cost_efficiency'].append(cost_efficiency)
        
        # Log statistics periodically
        if self.num_timesteps % self.eval_freq == 0:
            self._log_stats()
            
        return True
    
    def _log_stats(self):
        """Log training statistics"""
        
        if self.stopping_stats['avg_rounds']:
            recent_rounds = self.stopping_stats['avg_rounds'][-100:]
            self.logger.record("train/avg_stopping_rounds", np.mean(recent_rounds))
            self.logger.record("train/std_stopping_rounds", np.std(recent_rounds))
            
        if self.stopping_stats['cost_efficiency']:
            recent_efficiency = self.stopping_stats['cost_efficiency'][-100:]
            self.logger.record("train/cost_efficiency", np.mean(recent_efficiency))

class StoppingPolicy:
    """
    Main interface for stopping policy
    
    Handles training, evaluation, and inference for consensus stopping decisions.
    """
    
    def __init__(self,
                 dataset_split: DatasetSplit,
                 model_save_path: str = "./models/stopping_policy",
                 config: Dict[str, Any] = None):
        
        self.dataset_split = dataset_split
        self.model_save_path = Path(model_save_path)
        self.model_save_path.mkdir(parents=True, exist_ok=True)
        
        # Training configuration
        self.config = config or {
            'learning_rate': 1e-4,
            'buffer_size': 50000,
            'learning_starts': 1000,
            'batch_size': 64,
            'gamma': 0.99,
            'train_freq': 4,
            'gradient_steps': 1,
            'target_update_interval': 1000,
            'exploration_fraction': 0.2,
            'exploration_initial_eps': 1.0,
            'exploration_final_eps': 0.05,
            'total_timesteps': 50000,
        }
        
        # Create environments
        self.train_env = self._create_environment(self.dataset_split.train_samples)
        self.eval_env = self._create_environment(self.dataset_split.validation_samples)
        
        # Initialize policy
        self.policy = None
        
        self.logger = logging.getLogger(__name__)
    
    def _create_environment(self, samples: List[ContractSample]) -> gym.Env:
        """Create consensus stopping environment"""
        
        env = ConsensusEnvironment(samples)
        env = Monitor(env)
        return env
    
    def train(self, total_timesteps: int = None) -> Dict[str, Any]:
        """Train the stopping policy"""
        
        timesteps = total_timesteps or self.config['total_timesteps']
        
        self.logger.info(f"Training stopping policy for {timesteps} timesteps")
        
        # Initialize DQN policy
        self.policy = DQN(
            policy="MlpPolicy",
            env=self.train_env,
            learning_rate=self.config['learning_rate'],
            buffer_size=self.config['buffer_size'],
            learning_starts=self.config['learning_starts'],
            batch_size=self.config['batch_size'],
            gamma=self.config['gamma'],
            train_freq=self.config['train_freq'],
            gradient_steps=self.config['gradient_steps'],
            target_update_interval=self.config['target_update_interval'],
            exploration_fraction=self.config['exploration_fraction'],
            exploration_initial_eps=self.config['exploration_initial_eps'],
            exploration_final_eps=self.config['exploration_final_eps'],
            verbose=1,
            tensorboard_log=str(self.model_save_path / "tensorboard"),
        )
        
        # Set up callbacks
        callback = StoppingPolicyCallback(
            eval_env=self.eval_env,
            eval_freq=1000,
            verbose=1
        )
        
        # Train policy
        self.policy.learn(
            total_timesteps=timesteps,
            callback=callback,
            progress_bar=True
        )
        
        # Save trained policy
        model_path = self.model_save_path / "stopping_policy.zip"
        self.policy.save(str(model_path))
        
        self.logger.info(f"Stopping policy saved to {model_path}")
        
        return {
            'timesteps': timesteps,
            'model_path': str(model_path),
            'config': self.config,
        }
    
    def load_policy(self, model_path: str = None):
        """Load trained policy"""
        
        if model_path is None:
            model_path = self.model_save_path / "stopping_policy.zip"
        
        self.policy = DQN.load(str(model_path), env=self.train_env)
        self.logger.info(f"Stopping policy loaded from {model_path}")
    
    def predict(self, audit_state: AuditState, deterministic: bool = True) -> Tuple[bool, float]:
        """Predict whether to stop consensus rounds"""
        
        if self.policy is None:
            raise ValueError("Policy not loaded. Call train() or load_policy() first.")
        
        # Get prediction
        obs = audit_state.to_vector().reshape(1, -1)
        action, _ = self.policy.predict(obs, deterministic=deterministic)
        
        # action: 0=CONTINUE, 1=STOP
        should_stop = bool(action)
        
        # Get confidence (Q-value difference)
        with torch.no_grad():
            obs_tensor = torch.FloatTensor(obs)
            q_values = self.policy.q_net(obs_tensor)
            confidence = float(torch.abs(q_values[0, 1] - q_values[0, 0]).item())
        
        return should_stop, confidence
    
    def evaluate(self, test_samples: List[ContractSample] = None, n_episodes: int = 100) -> Dict[str, Any]:
        """Evaluate trained stopping policy"""
        
        if self.policy is None:
            raise ValueError("Policy not loaded. Call train() or load_policy() first.")
        
        test_samples = test_samples or self.dataset_split.test_samples
        eval_env = self._create_environment(test_samples[:n_episodes])
        
        # Run evaluation episodes
        episode_rounds = []
        episode_costs = []
        episode_confidences = []
        
        for episode in range(n_episodes):
            obs, _ = eval_env.reset()
            done = False
            step_count = 0
            
            while not done and step_count < 15:  # Safety limit
                action, _ = self.policy.predict(obs, deterministic=True)
                obs, reward, done, _, info = eval_env.step(action)
                step_count += 1
                
                if done and 'total_rounds' in info:
                    episode_rounds.append(info['total_rounds'])
                    
                if done and 'total_cost' in info:
                    episode_costs.append(info['total_cost'])
                    
                if done and 'final_confidence' in info:
                    episode_confidences.append(info['final_confidence'])
        
        return {
            'mean_stopping_rounds': np.mean(episode_rounds) if episode_rounds else 0,
            'std_stopping_rounds': np.std(episode_rounds) if episode_rounds else 0,
            'mean_total_cost': np.mean(episode_costs) if episode_costs else 0,
            'mean_final_confidence': np.mean(episode_confidences) if episode_confidences else 0,
            'cost_efficiency': np.mean([c/max(0.01, cost) for c, cost in zip(episode_confidences, episode_costs)]) if episode_confidences and episode_costs else 0,
            'episodes_evaluated': len(episode_rounds),
        }

# Example usage and testing
if __name__ == "__main__":
    # Set up logging
    logging.basicConfig(level=logging.INFO)
    
    # Create synthetic dataset for testing
    from evaluation.benchmark_datasets import BenchmarkDatasetManager
    
    print("Generating synthetic dataset...")
    manager = BenchmarkDatasetManager()
    dataset_split = manager.generate_synthetic_dataset(n_contracts=1000)
    
    print("Training stopping policy...")
    stopping_policy = StoppingPolicy(dataset_split)
    
    # Train policy
    training_summary = stopping_policy.train(total_timesteps=10000)
    print("Training Summary:", json.dumps(training_summary, indent=2))
    
    # Evaluate policy
    eval_results = stopping_policy.evaluate(n_episodes=100)
    print("Evaluation Results:", json.dumps(eval_results, indent=2))
    
    # Test prediction
    sample_contract = dataset_split.test_samples[0]
    test_state = AuditState(
        contract_features=sample_contract.features,
        current_round=3,
        cumulative_cost=0.15,
        vulnerabilities_found=2,
        confidence_scores=[0.6, 0.75, 0.82]
    )
    
    should_stop, confidence = stopping_policy.predict(test_state)
    print(f"Sample prediction: {'STOP' if should_stop else 'CONTINUE'} (confidence: {confidence:.3f})")