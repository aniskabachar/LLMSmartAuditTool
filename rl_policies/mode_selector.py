"""
RL Mode Selector Policy Implementation
====================================

This module implements the reinforcement learning policy that learns to select
the optimal audit mode (BA/TA/Hybrid) based on contract characteristics.

Key Goals:
1. Replace hardcoded CLI --config selection with adaptive RL policy
2. Learn cost-accuracy tradeoffs for different contract types  
3. Route simple contracts to BA, complex contracts to TA/Hybrid
4. Approach Hybrid-mode coverage at fraction of Hybrid-mode cost

Architecture:
- State: Contract features (22 dimensions) + cost/time constraints
- Action: Discrete choice among {BA, TA, HYBRID}
- Reward: Accuracy gain - cost penalty + efficiency bonus
- Policy: PPO with custom feature extraction network
"""

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from typing import Dict, List, Tuple, Optional, Any
import gymnasium as gym
from dataclasses import dataclass
import json
import logging
from pathlib import Path

from stable_baselines3 import PPO
from stable_baselines3.common.policies import ActorCriticPolicy
from stable_baselines3.common.torch_layers import BaseFeaturesExtractor
from stable_baselines3.common.distributions import CategoricalDistribution
from stable_baselines3.common.type_aliases import Schedule
from stable_baselines3.common.callbacks import BaseCallback, EvalCallback
from stable_baselines3.common.monitor import Monitor

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from rl_environment.rl_architecture import ContractFeatures, AuditMode, AuditState
from evaluation.benchmark_datasets import ContractSample, DatasetSplit

@dataclass
class ModeSelectionReward:
    """Reward components for mode selection"""
    accuracy_reward: float
    cost_penalty: float
    efficiency_bonus: float
    coverage_reward: float
    total_reward: float

class ContractComplexityEncoder(BaseFeaturesExtractor):
    """
    Neural network for encoding contract complexity features
    
    Transforms the 27-dimensional state vector into learned representations
    that capture relevant patterns for mode selection.
    """
    
    def __init__(self, observation_space: gym.Space, features_dim: int = 256):
        super().__init__(observation_space, features_dim)
        
        input_dim = observation_space.shape[0]  # 27 features
        
        # Contract feature encoding layers
        self.contract_encoder = nn.Sequential(
            nn.Linear(input_dim, 128),
            nn.ReLU(),
            nn.BatchNorm1d(128),
            nn.Dropout(0.2),
            
            nn.Linear(128, 256), 
            nn.ReLU(),
            nn.BatchNorm1d(256),
            nn.Dropout(0.2),
            
            nn.Linear(256, features_dim),
            nn.ReLU()
        )
        
        # Attention mechanism for important features
        self.attention = nn.MultiheadAttention(
            embed_dim=features_dim,
            num_heads=8,
            dropout=0.1,
            batch_first=True
        )
        
        self.layer_norm = nn.LayerNorm(features_dim)
        
    def forward(self, observations: torch.Tensor) -> torch.Tensor:
        batch_size = observations.shape[0]
        
        # Encode contract features
        encoded = self.contract_encoder(observations)
        
        # Apply self-attention (treat features as sequence length 1)
        encoded_expanded = encoded.unsqueeze(1)  # [batch, 1, features_dim]
        attended, _ = self.attention(encoded_expanded, encoded_expanded, encoded_expanded)
        attended = attended.squeeze(1)  # [batch, features_dim]
        
        # Residual connection and layer norm
        output = self.layer_norm(encoded + attended)
        
        return output

class ModeSelectorPolicy(ActorCriticPolicy):
    """
    Custom actor-critic policy for mode selection
    
    Learns to map contract characteristics to optimal audit modes
    with explicit consideration of cost-accuracy tradeoffs.
    """
    
    def __init__(self, 
                 observation_space: gym.Space,
                 action_space: gym.Space,
                 lr_schedule: Schedule,
                 **kwargs):
        
        # Set custom feature extractor
        kwargs['features_extractor_class'] = ContractComplexityEncoder
        kwargs['features_extractor_kwargs'] = {'features_dim': 256}
        
        super().__init__(observation_space, action_space, lr_schedule, **kwargs)
        
        # Override policy networks for mode selection
        self._build_custom_networks()
        
    def _build_custom_networks(self):
        """Build custom actor and critic networks"""
        
        feature_dim = 256
        
        # Actor network (policy) - outputs mode probabilities
        self.action_net = nn.Sequential(
            nn.Linear(feature_dim, 128),
            nn.ReLU(),
            nn.Dropout(0.1),
            
            nn.Linear(128, 64),
            nn.ReLU(),
            nn.Dropout(0.1),
            
            nn.Linear(64, 32),
            nn.ReLU(),
            
            nn.Linear(32, 3)  # BA, TA, HYBRID
        )
        
        # Critic network (value function)
        self.value_net = nn.Sequential(
            nn.Linear(feature_dim, 128),
            nn.ReLU(),
            nn.Dropout(0.1),
            
            nn.Linear(128, 64),
            nn.ReLU(),
            nn.Dropout(0.1),
            
            nn.Linear(64, 32),
            nn.ReLU(),
            
            nn.Linear(32, 1)
        )
        
    def forward(self, obs: torch.Tensor, deterministic: bool = False):
        """Forward pass through policy network"""
        
        # Extract features
        features = self.extract_features(obs)
        
        # Get action logits
        action_logits = self.action_net(features)
        
        # Get value estimate
        value = self.value_net(features)
        
        # Create categorical distribution
        distribution = CategoricalDistribution(action_dim=3)
        distribution = distribution.proba_distribution(action_logits=action_logits)
        
        # Sample action
        if deterministic:
            action = torch.argmax(action_logits, dim=1)
        else:
            action = distribution.sample()
        
        # Get log probability
        log_prob = distribution.log_prob(action)
        
        return action, value, log_prob
        
    def evaluate_actions(self, obs: torch.Tensor, actions: torch.Tensor):
        """Evaluate actions for policy updates"""
        
        features = self.extract_features(obs)
        action_logits = self.action_net(features)
        value = self.value_net(features)
        
        distribution = CategoricalDistribution(action_dim=3)
        distribution = distribution.proba_distribution(action_logits=action_logits)
        
        log_prob = distribution.log_prob(actions)
        entropy = distribution.entropy()
        
        return value, log_prob, entropy

class ModeSelectionEnvironment(gym.Env):
    """
    Specialized environment for training mode selection policy
    
    Focuses specifically on the mode selection task with contract-specific
    reward shaping and realistic cost-accuracy modeling.
    """
    
    def __init__(self,
                 dataset_samples: List[ContractSample],
                 cost_model: Dict[str, float] = None,
                 accuracy_model: Dict[str, Dict[str, float]] = None):
        
        super().__init__()
        
        self.dataset_samples = dataset_samples
        self.current_sample_idx = 0
        self.current_contract = None
        
        # Cost model (from base paper)
        self.cost_model = cost_model or {
            'BA': 0.21,      # $0.21 per contract
            'TA': 0.98,      # $0.98 per contract  
            'HYBRID': 1.19   # $1.19 per contract
        }
        
        # Accuracy model by contract complexity and vulnerability type
        # These would be learned from baseline experiments
        self.accuracy_model = accuracy_model or self._get_default_accuracy_model()
        
        # Define spaces
        # State: 27-dimensional (22 contract features + 5 temporal/cost features)
        self.observation_space = gym.spaces.Box(
            low=0.0, high=1.0, shape=(27,), dtype=np.float32
        )
        
        # Action: 3 discrete modes {BA: 0, TA: 1, HYBRID: 2}
        self.action_space = gym.spaces.Discrete(3)
        
        # Episode tracking
        self.episode_step = 0
        self.max_episode_steps = 1
        
    def _get_default_accuracy_model(self) -> Dict[str, Dict[str, float]]:
        """Default accuracy model based on paper results"""
        
        return {
            'BA': {
                'simple_contracts': 0.65,    # Good for broad analysis
                'medium_contracts': 0.55,    # May miss complex patterns
                'complex_contracts': 0.45,   # Struggles with complexity
                'token_contracts': 0.60,     # Decent on standard patterns
                'defi_contracts': 0.40,      # Misses novel DeFi patterns
            },
            'TA': {
                'simple_contracts': 0.95,    # Excellent on known patterns
                'medium_contracts': 0.90,    # Very good accuracy
                'complex_contracts': 0.85,   # Good but may miss novel issues
                'token_contracts': 0.98,     # Excellent on token standards
                'defi_contracts': 0.80,      # Good on known DeFi patterns
            },
            'HYBRID': {
                'simple_contracts': 0.85,    # Overkill but comprehensive
                'medium_contracts': 0.88,    # Good balance
                'complex_contracts': 0.92,   # Best for complex contracts
                'token_contracts': 0.95,     # Comprehensive coverage
                'defi_contracts': 0.89,      # Best for novel DeFi issues
            }
        }
    
    def reset(self, seed=None, options=None):
        """Reset environment with new contract sample"""
        
        super().reset(seed=seed)
        
        # Select next contract sample
        if options and 'sample_idx' in options:
            self.current_sample_idx = options['sample_idx']
        else:
            self.current_sample_idx = np.random.randint(len(self.dataset_samples))
        
        self.current_contract = self.dataset_samples[self.current_sample_idx]
        
        # Create audit state
        audit_state = AuditState(
            contract_features=self.current_contract.features,
            current_round=0,
            max_rounds=10,
            cumulative_cost=0.0,
            vulnerabilities_found=0,
        )
        
        self.episode_step = 0
        
        return audit_state.to_vector(), {}
    
    def step(self, action: int):
        """Execute mode selection and return reward"""
        
        # Map action to mode
        mode_map = {0: 'BA', 1: 'TA', 2: 'HYBRID'}
        selected_mode = mode_map[action]
        
        # Calculate reward components
        reward_components = self._calculate_mode_reward(selected_mode)
        
        self.episode_step += 1
        done = True  # Mode selection is single-step decision
        
        # Return next state (same as current for single-step)
        audit_state = AuditState(
            contract_features=self.current_contract.features,
            current_round=1,
            cumulative_cost=self.cost_model[selected_mode],
            vulnerabilities_found=len(self.current_contract.vulnerabilities),
        )
        
        info = {
            'selected_mode': selected_mode,
            'reward_components': reward_components,
            'contract_complexity': self._classify_contract_complexity(),
            'ground_truth_vulnerabilities': len(self.current_contract.vulnerabilities),
            'contract_type': self._classify_contract_type(),
        }
        
        return audit_state.to_vector(), reward_components.total_reward, done, False, info
    
    def _calculate_mode_reward(self, selected_mode: str) -> ModeSelectionReward:
        """Calculate reward for mode selection decision"""
        
        # Classify contract
        complexity_class = self._classify_contract_complexity()
        contract_type = self._classify_contract_type()
        
        # Get expected accuracy for this mode and contract type
        expected_accuracy = self.accuracy_model[selected_mode].get(
            contract_type, 
            self.accuracy_model[selected_mode].get(complexity_class, 0.5)
        )
        
        # Calculate cost
        cost = self.cost_model[selected_mode]
        
        # Reward components
        accuracy_reward = expected_accuracy * 10.0  # Scale accuracy
        cost_penalty = cost * 3.0  # Penalize high costs
        
        # Efficiency bonus: reward for using cheaper mode when appropriate
        efficiency_bonus = 0.0
        if selected_mode == 'BA' and complexity_class == 'simple_contracts':
            efficiency_bonus = 2.0  # Bonus for efficient choice
        elif selected_mode == 'TA' and complexity_class == 'medium_contracts':
            efficiency_bonus = 1.5
        elif selected_mode == 'HYBRID' and complexity_class == 'complex_contracts':
            efficiency_bonus = 1.0
        else:
            # Penalty for suboptimal choices
            if selected_mode == 'HYBRID' and complexity_class == 'simple_contracts':
                efficiency_bonus = -2.0  # Overkill penalty
            elif selected_mode == 'BA' and complexity_class == 'complex_contracts':
                efficiency_bonus = -1.5  # Under-analysis penalty
        
        # Coverage reward: bonus for vulnerability detection
        num_vulnerabilities = len(self.current_contract.vulnerabilities)
        expected_detected = expected_accuracy * num_vulnerabilities
        coverage_reward = expected_detected * 0.5
        
        total_reward = accuracy_reward - cost_penalty + efficiency_bonus + coverage_reward
        
        return ModeSelectionReward(
            accuracy_reward=accuracy_reward,
            cost_penalty=cost_penalty,
            efficiency_bonus=efficiency_bonus,
            coverage_reward=coverage_reward,
            total_reward=total_reward
        )
    
    def _classify_contract_complexity(self) -> str:
        """Classify contract complexity based on features"""
        
        features = self.current_contract.features
        
        # Simple heuristic based on multiple factors
        complexity_score = 0
        
        # Code size factor
        if features.lines_of_code > 500:
            complexity_score += 2
        elif features.lines_of_code > 200:
            complexity_score += 1
        
        # Function count factor  
        if features.function_count > 20:
            complexity_score += 2
        elif features.function_count > 10:
            complexity_score += 1
        
        # Complexity indicators
        if features.external_calls > 5:
            complexity_score += 1
        if features.loop_count > 3:
            complexity_score += 1
        if features.conditional_count > 15:
            complexity_score += 1
        if features.uses_assembly:
            complexity_score += 2
        if features.uses_delegatecall:
            complexity_score += 1
        if features.inheritance_depth > 3:
            complexity_score += 1
        
        # Classify based on score
        if complexity_score <= 2:
            return 'simple_contracts'
        elif complexity_score <= 5:
            return 'medium_contracts'
        else:
            return 'complex_contracts'
    
    def _classify_contract_type(self) -> str:
        """Classify contract type for accuracy modeling"""
        
        features = self.current_contract.features
        
        # Specific contract type detection
        if features.is_token_contract:
            return 'token_contracts'
        elif (features.handles_ether and 
              features.external_calls > 3 and 
              features.function_count > 15):
            return 'defi_contracts'
        else:
            # Fall back to complexity classification
            return self._classify_contract_complexity()

class ModeSelectionCallback(BaseCallback):
    """Training callback for mode selection policy"""
    
    def __init__(self, 
                 eval_env: gym.Env,
                 eval_freq: int = 1000,
                 verbose: int = 1):
        
        super().__init__(verbose)
        self.eval_env = eval_env
        self.eval_freq = eval_freq
        
        # Tracking metrics
        self.mode_selection_stats = {
            'BA': 0, 'TA': 0, 'HYBRID': 0
        }
        self.reward_history = []
        self.efficiency_scores = []
        
    def _on_step(self) -> bool:
        """Called after each step"""
        
        # Extract info from last step
        infos = self.locals.get('infos', [])
        
        for info in infos:
            if 'selected_mode' in info:
                mode = info['selected_mode']
                self.mode_selection_stats[mode] += 1
                
            if 'reward_components' in info:
                reward_comp = info['reward_components']
                self.reward_history.append(reward_comp.total_reward)
                
                # Calculate efficiency score
                efficiency = (reward_comp.accuracy_reward + reward_comp.coverage_reward) / max(0.1, reward_comp.cost_penalty)
                self.efficiency_scores.append(efficiency)
        
        # Log statistics periodically
        if self.num_timesteps % self.eval_freq == 0:
            self._log_training_stats()
            
        return True
    
    def _log_training_stats(self):
        """Log training statistics"""
        
        # Mode selection distribution
        total_selections = sum(self.mode_selection_stats.values())
        if total_selections > 0:
            for mode, count in self.mode_selection_stats.items():
                pct = count / total_selections
                self.logger.record(f"train/mode_{mode}_percentage", pct)
        
        # Reward statistics
        if self.reward_history:
            recent_rewards = self.reward_history[-100:]
            self.logger.record("train/mean_reward", np.mean(recent_rewards))
            self.logger.record("train/std_reward", np.std(recent_rewards))
            
        # Efficiency statistics
        if self.efficiency_scores:
            recent_efficiency = self.efficiency_scores[-100:]
            self.logger.record("train/mean_efficiency", np.mean(recent_efficiency))
            
        # Reset counters
        self.mode_selection_stats = {'BA': 0, 'TA': 0, 'HYBRID': 0}

class ModeSelector:
    """
    Main interface for mode selection policy
    
    Handles training, evaluation, and inference for the mode selection task.
    """
    
    def __init__(self, 
                 dataset_split: DatasetSplit,
                 model_save_path: str = "./models/mode_selector",
                 config: Dict[str, Any] = None):
        
        self.dataset_split = dataset_split
        self.model_save_path = Path(model_save_path)
        self.model_save_path.mkdir(parents=True, exist_ok=True)
        
        # Training configuration
        self.config = config or {
            'learning_rate': 3e-4,
            'batch_size': 64,
            'n_steps': 2048,
            'n_epochs': 10,
            'gamma': 0.99,
            'gae_lambda': 0.95,
            'clip_range': 0.2,
            'ent_coef': 0.01,
            'vf_coef': 0.5,
            'max_grad_norm': 0.5,
            'total_timesteps': 50000,
        }
        
        # Create environments
        self.train_env = self._create_environment(self.dataset_split.train_samples)
        self.eval_env = self._create_environment(self.dataset_split.validation_samples)
        
        # Initialize policy
        self.policy = None
        
        self.logger = logging.getLogger(__name__)
        
    def _create_environment(self, samples: List[ContractSample]) -> gym.Env:
        """Create mode selection environment"""
        
        env = ModeSelectionEnvironment(samples)
        env = Monitor(env)  # Wrap with monitor for logging
        return env
        
    def train(self, total_timesteps: int = None) -> Dict[str, Any]:
        """Train the mode selection policy"""
        
        timesteps = total_timesteps or self.config['total_timesteps']
        
        self.logger.info(f"Training mode selector for {timesteps} timesteps")
        
        # Initialize PPO policy
        self.policy = PPO(
            policy=ModeSelectorPolicy,
            env=self.train_env,
            learning_rate=self.config['learning_rate'],
            n_steps=self.config['n_steps'],
            batch_size=self.config['batch_size'],
            n_epochs=self.config['n_epochs'],
            gamma=self.config['gamma'],
            gae_lambda=self.config['gae_lambda'],
            clip_range=self.config['clip_range'],
            ent_coef=self.config['ent_coef'],
            vf_coef=self.config['vf_coef'],
            max_grad_norm=self.config['max_grad_norm'],
            verbose=1,
            tensorboard_log=str(self.model_save_path / "tensorboard"),
        )
        
        # Set up callbacks
        callback = ModeSelectionCallback(
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
        model_path = self.model_save_path / "mode_selector.zip"
        self.policy.save(str(model_path))
        
        self.logger.info(f"Mode selector saved to {model_path}")
        
        # Return training summary
        return {
            'timesteps': timesteps,
            'model_path': str(model_path),
            'config': self.config,
        }
    
    def load_policy(self, model_path: str = None):
        """Load trained policy"""
        
        if model_path is None:
            model_path = self.model_save_path / "mode_selector.zip"
        
        self.policy = PPO.load(str(model_path), env=self.train_env)
        self.logger.info(f"Mode selector loaded from {model_path}")
    
    def predict(self, contract_features: ContractFeatures, deterministic: bool = True) -> Tuple[AuditMode, float]:
        """Predict optimal mode for contract"""
        
        if self.policy is None:
            raise ValueError("Policy not loaded. Call train() or load_policy() first.")
        
        # Create audit state
        audit_state = AuditState(
            contract_features=contract_features,
            current_round=0,
            cumulative_cost=0.0,
            vulnerabilities_found=0,
        )
        
        # Get prediction
        obs = audit_state.to_vector().reshape(1, -1)
        action, _ = self.policy.predict(obs, deterministic=deterministic)
        
        # Map action to mode
        mode_map = {0: AuditMode.BA, 1: AuditMode.TA, 2: AuditMode.HYBRID}
        selected_mode = mode_map[int(action)]
        
        # Get confidence score (based on action probability)
        with torch.no_grad():
            obs_tensor = torch.FloatTensor(obs)
            features = self.policy.policy.extract_features(obs_tensor)
            action_logits = self.policy.policy.action_net(features)
            action_probs = F.softmax(action_logits, dim=1)
            confidence = float(action_probs[0, int(action)].item())
        
        return selected_mode, confidence
    
    def evaluate(self, test_samples: List[ContractSample] = None, n_episodes: int = 100) -> Dict[str, Any]:
        """Evaluate trained policy"""
        
        if self.policy is None:
            raise ValueError("Policy not loaded. Call train() or load_policy() first.")
        
        test_samples = test_samples or self.dataset_split.test_samples
        eval_env = self._create_environment(test_samples[:n_episodes])
        
        # Run evaluation episodes
        total_reward = 0
        mode_counts = {'BA': 0, 'TA': 0, 'HYBRID': 0}
        efficiency_scores = []
        
        for episode in range(n_episodes):
            obs, _ = eval_env.reset()
            action, _ = self.policy.predict(obs, deterministic=True)
            obs, reward, done, _, info = eval_env.step(action)
            
            total_reward += reward
            
            if 'selected_mode' in info:
                mode_counts[info['selected_mode']] += 1
                
            if 'reward_components' in info:
                reward_comp = info['reward_components']
                efficiency = (reward_comp.accuracy_reward + reward_comp.coverage_reward) / max(0.1, reward_comp.cost_penalty)
                efficiency_scores.append(efficiency)
        
        return {
            'mean_reward': total_reward / n_episodes,
            'mode_distribution': {k: v/n_episodes for k, v in mode_counts.items()},
            'mean_efficiency': np.mean(efficiency_scores) if efficiency_scores else 0,
            'episodes_evaluated': n_episodes,
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
    
    print("Training mode selector...")
    mode_selector = ModeSelector(dataset_split)
    
    # Train policy
    training_summary = mode_selector.train(total_timesteps=10000)
    print("Training Summary:", json.dumps(training_summary, indent=2))
    
    # Evaluate policy  
    eval_results = mode_selector.evaluate(n_episodes=100)
    print("Evaluation Results:", json.dumps(eval_results, indent=2))
    
    # Test prediction
    sample_contract = dataset_split.test_samples[0]
    predicted_mode, confidence = mode_selector.predict(sample_contract.features)
    print(f"Sample prediction: {predicted_mode.value} (confidence: {confidence:.3f})")