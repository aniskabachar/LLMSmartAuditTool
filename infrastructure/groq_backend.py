"""
Groq LLaMA-3.3-70B Backend Integration
=====================================

This module provides integration with Groq's high-performance inference platform
for cost-effective LLM operations during RL training and evaluation.

Key Features:
1. Fast inference optimized for RL rollouts (high throughput, low latency)
2. Cost tracking and budget management for training runs
3. Rate limiting and error handling for production stability
4. Token usage optimization and caching strategies
5. Batch processing for efficient multi-contract analysis

Groq Benefits for RL Training:
- ~10x faster inference than standard GPT-4 API
- Significantly lower cost per token (~$0.27/1M tokens vs $30/1M tokens)
- Predictable performance for RL environment step timing
- High throughput needed for policy gradient updates
"""

import os
import time
import json
import logging
import asyncio
from typing import Dict, List, Tuple, Optional, Any, Union
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
import numpy as np
from pathlib import Path
import hashlib

try:
    from groq import Groq
    import groq
    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False
    print("Groq library not available. Install with: pip install groq")

# Fallback imports for development/testing
try:
    import openai
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

@dataclass
class GroqUsageStats:
    """Track Groq API usage statistics"""
    total_requests: int = 0
    total_tokens_input: int = 0
    total_tokens_output: int = 0
    total_cost_usd: float = 0.0
    total_time_seconds: float = 0.0
    errors: int = 0
    rate_limit_hits: int = 0
    
    def add_request(self, input_tokens: int, output_tokens: int, 
                   time_seconds: float, cost_usd: float):
        """Add statistics from a single request"""
        self.total_requests += 1
        self.total_tokens_input += input_tokens
        self.total_tokens_output += output_tokens
        self.total_cost_usd += cost_usd
        self.total_time_seconds += time_seconds
    
    def get_summary(self) -> Dict[str, Any]:
        """Get summary statistics"""
        avg_time = self.total_time_seconds / max(1, self.total_requests)
        tokens_per_second = (self.total_tokens_input + self.total_tokens_output) / max(0.01, self.total_time_seconds)
        
        return {
            'requests': self.total_requests,
            'input_tokens': self.total_tokens_input,
            'output_tokens': self.total_tokens_output,
            'total_tokens': self.total_tokens_input + self.total_tokens_output,
            'total_cost_usd': self.total_cost_usd,
            'total_time_seconds': self.total_time_seconds,
            'avg_request_time': avg_time,
            'tokens_per_second': tokens_per_second,
            'cost_per_request': self.total_cost_usd / max(1, self.total_requests),
            'errors': self.errors,
            'rate_limit_hits': self.rate_limit_hits,
        }

@dataclass
class GroqConfig:
    """Configuration for Groq backend"""
    api_key: str
    model_name: str = "llama-3.3-70b-versatile"  # Groq's LLaMA-3.3-70B model
    max_tokens: int = 4096
    temperature: float = 0.1  # Low temperature for consistent RL training
    timeout_seconds: int = 30
    max_retries: int = 3
    rate_limit_rpm: int = 300  # Requests per minute limit
    rate_limit_tpm: int = 100000  # Tokens per minute limit
    cost_per_input_token: float = 0.00000027  # Groq pricing (approximate)
    cost_per_output_token: float = 0.00000027
    enable_caching: bool = True
    cache_dir: str = "./cache/groq"

class GroqRateLimiter:
    """Rate limiter for Groq API calls"""
    
    def __init__(self, requests_per_minute: int = 300, tokens_per_minute: int = 100000):
        self.rpm_limit = requests_per_minute
        self.tpm_limit = tokens_per_minute
        
        # Sliding window tracking
        self.request_timestamps = []
        self.token_usage = []  # (timestamp, token_count) pairs
        
        self.logger = logging.getLogger(__name__)
    
    def can_make_request(self, estimated_tokens: int = 0) -> bool:
        """Check if request can be made without hitting rate limits"""
        
        now = time.time()
        one_minute_ago = now - 60
        
        # Clean old entries
        self.request_timestamps = [t for t in self.request_timestamps if t > one_minute_ago]
        self.token_usage = [(t, tokens) for t, tokens in self.token_usage if t > one_minute_ago]
        
        # Check request rate limit
        if len(self.request_timestamps) >= self.rpm_limit:
            return False
        
        # Check token rate limit
        current_tokens = sum(tokens for _, tokens in self.token_usage)
        if current_tokens + estimated_tokens > self.tpm_limit:
            return False
        
        return True
    
    def record_request(self, tokens_used: int):
        """Record a successful request"""
        now = time.time()
        self.request_timestamps.append(now)
        self.token_usage.append((now, tokens_used))
    
    def wait_time_until_available(self, estimated_tokens: int = 0) -> float:
        """Calculate wait time until request can be made"""
        
        if self.can_make_request(estimated_tokens):
            return 0.0
        
        now = time.time()
        one_minute_ago = now - 60
        
        # Find when oldest request/tokens will expire
        wait_times = []
        
        # Request rate limit wait time
        if len(self.request_timestamps) >= self.rpm_limit:
            oldest_request = min(self.request_timestamps)
            wait_times.append(oldest_request + 60 - now)
        
        # Token rate limit wait time
        current_tokens = sum(tokens for _, tokens in self.token_usage)
        if current_tokens + estimated_tokens > self.tpm_limit:
            # Find when enough tokens will expire
            sorted_usage = sorted(self.token_usage)
            tokens_to_free = current_tokens + estimated_tokens - self.tpm_limit
            tokens_freed = 0
            
            for timestamp, tokens in sorted_usage:
                tokens_freed += tokens
                if tokens_freed >= tokens_to_free:
                    wait_times.append(timestamp + 60 - now)
                    break
        
        return max(wait_times) if wait_times else 0.0

class GroqResponseCache:
    """Cache for Groq API responses to reduce costs during development"""
    
    def __init__(self, cache_dir: str = "./cache/groq"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
    def _get_cache_key(self, messages: List[Dict], model: str, temperature: float) -> str:
        """Generate cache key for request"""
        
        # Create deterministic hash of request parameters
        request_data = {
            'messages': messages,
            'model': model,
            'temperature': round(temperature, 3)  # Round to avoid float precision issues
        }
        
        request_str = json.dumps(request_data, sort_keys=True)
        return hashlib.md5(request_str.encode()).hexdigest()
    
    def get_cached_response(self, messages: List[Dict], model: str, temperature: float) -> Optional[Dict]:
        """Get cached response if available"""
        
        cache_key = self._get_cache_key(messages, model, temperature)
        cache_file = self.cache_dir / f"{cache_key}.json"
        
        if cache_file.exists():
            try:
                with open(cache_file, 'r') as f:
                    cached_data = json.load(f)
                
                # Check if cache is still valid (not too old)
                cache_time = datetime.fromisoformat(cached_data['timestamp'])
                if datetime.now() - cache_time < timedelta(days=7):  # 7 day cache expiry
                    return cached_data['response']
                    
            except Exception as e:
                logging.warning(f"Failed to read cache file {cache_file}: {e}")
        
        return None
    
    def cache_response(self, messages: List[Dict], model: str, temperature: float, response: Dict):
        """Cache API response"""
        
        cache_key = self._get_cache_key(messages, model, temperature)
        cache_file = self.cache_dir / f"{cache_key}.json"
        
        cache_data = {
            'timestamp': datetime.now().isoformat(),
            'request': {
                'messages': messages,
                'model': model,
                'temperature': temperature
            },
            'response': response
        }
        
        try:
            with open(cache_file, 'w') as f:
                json.dump(cache_data, f, indent=2)
        except Exception as e:
            logging.warning(f"Failed to cache response: {e}")

class GroqBackend:
    """
    High-performance Groq backend for RL training
    
    Provides optimized LLM inference for smart contract auditing with
    cost tracking, rate limiting, and caching for efficient RL training.
    """
    
    def __init__(self, config: GroqConfig):
        self.config = config
        
        # Initialize Groq client
        if GROQ_AVAILABLE:
            self.groq_client = Groq(api_key=config.api_key)
        else:
            self.groq_client = None
            logging.warning("Groq library not available, using fallback")
        
        # Initialize components
        self.rate_limiter = GroqRateLimiter(
            requests_per_minute=config.rate_limit_rpm,
            tokens_per_minute=config.rate_limit_tpm
        )
        
        self.cache = GroqResponseCache(config.cache_dir) if config.enable_caching else None
        self.usage_stats = GroqUsageStats()
        
        self.logger = logging.getLogger(__name__)
        
    def estimate_tokens(self, text: str) -> int:
        """Estimate token count for text (rough approximation)"""
        # Rough estimation: ~4 characters per token for English text
        return len(text) // 4
    
    def create_chat_completion(self, 
                             messages: List[Dict[str, str]],
                             max_tokens: Optional[int] = None,
                             temperature: Optional[float] = None) -> Dict[str, Any]:
        """
        Create chat completion with rate limiting and caching
        
        Args:
            messages: List of message dictionaries with 'role' and 'content'
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature
            
        Returns:
            Response dictionary with usage statistics
        """
        
        # Use config defaults if not specified
        max_tokens = max_tokens or self.config.max_tokens
        temperature = temperature if temperature is not None else self.config.temperature
        
        # Check cache first
        if self.cache:
            cached_response = self.cache.get_cached_response(messages, self.config.model_name, temperature)
            if cached_response:
                self.logger.debug("Using cached response")
                return cached_response
        
        # Estimate token usage for rate limiting
        estimated_input_tokens = sum(self.estimate_tokens(msg['content']) for msg in messages)
        estimated_total_tokens = estimated_input_tokens + max_tokens
        
        # Wait if rate limited
        wait_time = self.rate_limiter.wait_time_until_available(estimated_total_tokens)
        if wait_time > 0:
            self.logger.info(f"Rate limited, waiting {wait_time:.1f} seconds")
            time.sleep(wait_time)
            self.usage_stats.rate_limit_hits += 1
        
        # Make API call
        start_time = time.time()
        
        try:
            if self.groq_client:
                # Use Groq API
                response = self._call_groq_api(messages, max_tokens, temperature)
            else:
                # Fallback to OpenAI API for development
                response = self._call_fallback_api(messages, max_tokens, temperature)
            
            request_time = time.time() - start_time
            
            # Extract usage information
            usage = response.get('usage', {})
            input_tokens = usage.get('prompt_tokens', estimated_input_tokens)
            output_tokens = usage.get('completion_tokens', 0)
            
            # Calculate cost
            cost = (input_tokens * self.config.cost_per_input_token + 
                   output_tokens * self.config.cost_per_output_token)
            
            # Record usage
            self.usage_stats.add_request(input_tokens, output_tokens, request_time, cost)
            self.rate_limiter.record_request(input_tokens + output_tokens)
            
            # Cache successful response
            if self.cache:
                self.cache.cache_response(messages, self.config.model_name, temperature, response)
            
            self.logger.debug(f"API call completed in {request_time:.2f}s, "
                            f"tokens: {input_tokens + output_tokens}, cost: ${cost:.4f}")
            
            return response
            
        except Exception as e:
            self.usage_stats.errors += 1
            self.logger.error(f"API call failed after {time.time() - start_time:.2f}s: {e}")
            raise
    
    def _call_groq_api(self, messages: List[Dict], max_tokens: int, temperature: float) -> Dict:
        """Call Groq API"""
        
        try:
            response = self.groq_client.chat.completions.create(
                model=self.config.model_name,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                timeout=self.config.timeout_seconds
            )
            
            # Convert response to dictionary format
            return {
                'choices': [{
                    'message': {
                        'role': 'assistant',
                        'content': response.choices[0].message.content
                    },
                    'finish_reason': response.choices[0].finish_reason
                }],
                'usage': {
                    'prompt_tokens': response.usage.prompt_tokens,
                    'completion_tokens': response.usage.completion_tokens,
                    'total_tokens': response.usage.total_tokens
                }
            }
            
        except Exception as e:
            self.logger.error(f"Groq API call failed: {e}")
            raise
    
    def _call_fallback_api(self, messages: List[Dict], max_tokens: int, temperature: float) -> Dict:
        """Fallback API call for development (uses OpenAI format)"""
        
        if not OPENAI_AVAILABLE:
            raise RuntimeError("Neither Groq nor OpenAI libraries available")
        
        # Simulate Groq response format
        estimated_input = sum(self.estimate_tokens(msg['content']) for msg in messages)
        estimated_output = min(max_tokens, 500)  # Simulate typical response
        
        # Create mock response for development
        mock_content = f"Mock audit response for development. Input tokens: ~{estimated_input}"
        
        return {
            'choices': [{
                'message': {
                    'role': 'assistant',
                    'content': mock_content
                },
                'finish_reason': 'stop'
            }],
            'usage': {
                'prompt_tokens': estimated_input,
                'completion_tokens': estimated_output,
                'total_tokens': estimated_input + estimated_output
            }
        }
    
    def batch_completions(self, 
                         batch_requests: List[Dict],
                         max_concurrent: int = 5) -> List[Dict]:
        """
        Process multiple chat completions concurrently
        
        Args:
            batch_requests: List of request dictionaries with 'messages', 'max_tokens', 'temperature'
            max_concurrent: Maximum concurrent requests
            
        Returns:
            List of response dictionaries in same order as requests
        """
        
        self.logger.info(f"Processing batch of {len(batch_requests)} requests "
                        f"with max_concurrent={max_concurrent}")
        
        import concurrent.futures
        
        def process_request(request):
            """Process single request from batch"""
            return self.create_chat_completion(
                messages=request['messages'],
                max_tokens=request.get('max_tokens'),
                temperature=request.get('temperature')
            )
        
        # Process requests with controlled concurrency
        results = []
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_concurrent) as executor:
            # Submit all requests
            future_to_index = {
                executor.submit(process_request, req): i 
                for i, req in enumerate(batch_requests)
            }
            
            # Collect results in order
            results = [None] * len(batch_requests)
            
            for future in concurrent.futures.as_completed(future_to_index):
                index = future_to_index[future]
                try:
                    results[index] = future.result()
                except Exception as e:
                    self.logger.error(f"Batch request {index} failed: {e}")
                    results[index] = None
        
        return results
    
    def get_usage_summary(self) -> Dict[str, Any]:
        """Get comprehensive usage statistics"""
        
        base_stats = self.usage_stats.get_summary()
        
        # Add additional metrics
        base_stats.update({
            'model': self.config.model_name,
            'cache_enabled': self.config.enable_caching,
            'rate_limits': {
                'rpm': self.config.rate_limit_rpm,
                'tpm': self.config.rate_limit_tpm
            }
        })
        
        return base_stats
    
    def estimate_batch_cost(self, batch_requests: List[Dict]) -> float:
        """Estimate cost for batch of requests"""
        
        total_input_tokens = 0
        total_max_output_tokens = 0
        
        for request in batch_requests:
            messages = request['messages']
            max_tokens = request.get('max_tokens', self.config.max_tokens)
            
            input_tokens = sum(self.estimate_tokens(msg['content']) for msg in messages)
            total_input_tokens += input_tokens
            total_max_output_tokens += max_tokens
        
        # Calculate estimated cost (use max output tokens for worst-case estimate)
        estimated_cost = (
            total_input_tokens * self.config.cost_per_input_token +
            total_max_output_tokens * self.config.cost_per_output_token
        )
        
        return estimated_cost
    
    def reset_usage_stats(self):
        """Reset usage statistics"""
        self.usage_stats = GroqUsageStats()

class GroqTrainingInfrastructure:
    """
    Training infrastructure optimized for RL with Groq backend
    
    Provides high-level interface for RL training with cost monitoring,
    performance optimization, and training session management.
    """
    
    def __init__(self, groq_backend: GroqBackend, training_config: Dict[str, Any] = None):
        self.groq_backend = groq_backend
        
        self.training_config = training_config or {
            'max_cost_per_session': 50.0,  # Maximum cost per training session
            'cost_warning_threshold': 0.8,  # Warn at 80% of budget
            'target_tokens_per_second': 1000,  # Target throughput
            'batch_size_optimization': True,  # Auto-optimize batch sizes
        }
        
        # Training session tracking
        self.current_session_cost = 0.0
        self.session_start_time = None
        
        self.logger = logging.getLogger(__name__)
    
    def start_training_session(self, session_name: str):
        """Start new training session with cost tracking"""
        
        self.session_start_time = time.time()
        self.current_session_cost = 0.0
        self.groq_backend.reset_usage_stats()
        
        self.logger.info(f"Started training session: {session_name}")
        self.logger.info(f"Budget: ${self.training_config['max_cost_per_session']:.2f}")
    
    def check_budget(self) -> Tuple[bool, float]:
        """
        Check if training can continue within budget
        
        Returns:
            (can_continue, remaining_budget)
        """
        
        current_cost = self.groq_backend.usage_stats.total_cost_usd
        max_cost = self.training_config['max_cost_per_session']
        remaining = max_cost - current_cost
        
        # Check warning threshold
        if current_cost / max_cost > self.training_config['cost_warning_threshold']:
            self.logger.warning(f"Training cost warning: ${current_cost:.2f} / ${max_cost:.2f} "
                              f"({current_cost/max_cost*100:.1f}%)")
        
        return remaining > 0, remaining
    
    def optimize_batch_size(self, base_batch_size: int, target_throughput: float = None) -> int:
        """
        Optimize batch size based on performance metrics
        
        Args:
            base_batch_size: Starting batch size
            target_throughput: Target tokens per second
            
        Returns:
            Optimized batch size
        """
        
        if not self.training_config['batch_size_optimization']:
            return base_batch_size
        
        target_throughput = target_throughput or self.training_config['target_tokens_per_second']
        
        # Get current performance metrics
        stats = self.groq_backend.get_usage_summary()
        current_throughput = stats.get('tokens_per_second', 0)
        
        if current_throughput == 0:
            return base_batch_size
        
        # Simple optimization: adjust batch size to meet throughput target
        if current_throughput < target_throughput * 0.8:
            # Increase batch size if throughput is low
            optimized_size = min(base_batch_size * 2, 32)  # Cap at reasonable limit
            self.logger.info(f"Increasing batch size from {base_batch_size} to {optimized_size} "
                           f"(current throughput: {current_throughput:.1f} tokens/s)")
            return optimized_size
            
        elif current_throughput > target_throughput * 1.5:
            # Decrease batch size if we're over-performing (may be hitting rate limits)
            optimized_size = max(base_batch_size // 2, 1)
            self.logger.info(f"Decreasing batch size from {base_batch_size} to {optimized_size} "
                           f"(current throughput: {current_throughput:.1f} tokens/s)")
            return optimized_size
        
        return base_batch_size
    
    def get_training_metrics(self) -> Dict[str, Any]:
        """Get comprehensive training session metrics"""
        
        usage_stats = self.groq_backend.get_usage_summary()
        
        session_time = time.time() - self.session_start_time if self.session_start_time else 0
        
        metrics = {
            'session_duration_seconds': session_time,
            'session_duration_minutes': session_time / 60,
            'usage_stats': usage_stats,
            'budget_utilization': {
                'spent': usage_stats['total_cost_usd'],
                'budget': self.training_config['max_cost_per_session'],
                'utilization_pct': usage_stats['total_cost_usd'] / self.training_config['max_cost_per_session'] * 100,
                'remaining': self.training_config['max_cost_per_session'] - usage_stats['total_cost_usd']
            },
            'performance': {
                'throughput_tokens_per_second': usage_stats.get('tokens_per_second', 0),
                'avg_request_time': usage_stats.get('avg_request_time', 0),
                'error_rate': usage_stats['errors'] / max(1, usage_stats['requests']),
                'rate_limit_hit_rate': usage_stats['rate_limit_hits'] / max(1, usage_stats['requests'])
            }
        }
        
        return metrics
    
    def end_training_session(self) -> Dict[str, Any]:
        """End training session and return final metrics"""
        
        final_metrics = self.get_training_metrics()
        
        self.logger.info("Training session completed")
        self.logger.info(f"Total cost: ${final_metrics['usage_stats']['total_cost_usd']:.2f}")
        self.logger.info(f"Total requests: {final_metrics['usage_stats']['requests']}")
        self.logger.info(f"Average throughput: {final_metrics['performance']['throughput_tokens_per_second']:.1f} tokens/s")
        
        return final_metrics

# Factory functions for easy setup
def create_groq_backend(api_key: str, 
                       model_name: str = "llama-3.3-70b-versatile",
                       enable_caching: bool = True) -> GroqBackend:
    """Create Groq backend with sensible defaults"""
    
    config = GroqConfig(
        api_key=api_key,
        model_name=model_name,
        enable_caching=enable_caching
    )
    
    return GroqBackend(config)

def create_training_infrastructure(api_key: str, 
                                 max_budget: float = 50.0) -> GroqTrainingInfrastructure:
    """Create complete training infrastructure"""
    
    backend = create_groq_backend(api_key)
    
    training_config = {
        'max_cost_per_session': max_budget,
        'cost_warning_threshold': 0.8,
        'target_tokens_per_second': 1000,
        'batch_size_optimization': True,
    }
    
    return GroqTrainingInfrastructure(backend, training_config)

# Example usage and testing
if __name__ == "__main__":
    # Set up logging
    logging.basicConfig(level=logging.INFO)
    
    # Example setup (requires GROQ_API_KEY environment variable)
    api_key = os.getenv('GROQ_API_KEY')
    
    if not api_key:
        print("GROQ_API_KEY environment variable not set")
        print("Example usage requires Groq API key")
    else:
        # Create training infrastructure
        training_infra = create_training_infrastructure(api_key, max_budget=10.0)
        
        # Start training session
        training_infra.start_training_session("test_session")
        
        # Example API call
        messages = [
            {"role": "system", "content": "You are a smart contract security auditor."},
            {"role": "user", "content": "Analyze this contract for reentrancy vulnerabilities: contract Test { mapping(address => uint) balances; function withdraw() public { msg.sender.call.value(balances[msg.sender])(\"\"); balances[msg.sender] = 0; } }"}
        ]
        
        try:
            response = training_infra.groq_backend.create_chat_completion(messages)
            print("Response:", response['choices'][0]['message']['content'][:200] + "...")
            
            # Check metrics
            metrics = training_infra.get_training_metrics()
            print(f"Cost so far: ${metrics['usage_stats']['total_cost_usd']:.4f}")
            
        except Exception as e:
            print(f"Error: {e}")
        
        finally:
            # End session
            final_metrics = training_infra.end_training_session()
            print("Final metrics:", json.dumps(final_metrics, indent=2, default=str))