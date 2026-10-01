"""
Ensemble Checker for Hybrid RL-Adaptive Auditing

This module implements a lightweight ensemble system that selectively invokes
Technical Analysis (TA) mode when complexity > 0.8 and budget > 15 to complement
Business Analyst (BA) mode findings for enhanced accuracy.

Key Features:
1. Complexity-based TA invocation (complexity > 0.8)
2. Budget-aware deployment (budget > 15 remaining)
3. Lightweight technical pattern verification
4. Confidence boosting for high-risk contracts
5. Minimal overhead while maximizing accuracy gains

Usage:
    ensemble = EnsembleChecker()
    should_use_ta = ensemble.should_invoke_ta(contract_features, current_budget, ba_results)
    if should_use_ta:
        ta_results = ensemble.invoke_lightweight_ta(contract_code)
        final_results = ensemble.merge_results(ba_results, ta_results)
"""

import logging
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass
import re
import time
import asyncio

from rl_environment.contract_analyzer import ContractAnalyzer, ContractFeatures
from camel.agents import ChatAgent
from camel.messages import BaseMessage
from camel.configs import ChatGPTConfig

logger = logging.getLogger(__name__)

@dataclass
class EnsembleConfig:
    """Configuration for ensemble checker"""
    
    # Thresholds for TA invocation
    complexity_threshold: float = 0.8
    budget_threshold: float = 15.0
    
    # Lightweight TA configuration
    max_ta_patterns: int = 10  # Limit pattern checks for speed
    ta_timeout: int = 30  # Max seconds for TA analysis
    confidence_boost_threshold: float = 0.7  # When to boost confidence
    
    # Ensemble strategy
    merge_strategy: str = "weighted_union"  # "union", "intersection", "weighted_union"
    ba_weight: float = 0.7
    ta_weight: float = 0.3

@dataclass
class EnsembleDecision:
    """Decision result for ensemble invocation"""
    
    should_invoke_ta: bool
    reason: str
    estimated_cost: float
    confidence: float
    complexity_score: float
    budget_remaining: float

class EnsembleChecker:
    """
    Lightweight ensemble system for selective TA invocation
    
    Analyzes contract complexity and budget constraints to determine
    when Technical Analysis should complement Business Analyst findings.
    """
    
    def __init__(self, config: Optional[EnsembleConfig] = None):
        self.config = config or EnsembleConfig()
        self.analyzer = ContractAnalyzer()
        
        # Lightweight TA patterns for quick scanning
        self.critical_patterns = [
            # High-impact security patterns that TA excels at detecting
            r'\brevert\s*\(',  # Proper error handling
            r'\brequire\s*\(',  # Input validation  
            r'\bassert\s*\(',  # State validation
            r'\bsafeTransfer',  # Safe token operations
            r'\bReentrancyGuard',  # Reentrancy protection
            r'\bonlyOwner',  # Access control
            r'\buint256\s+private',  # Private state variables
            r'\bpure\b|\bview\b',  # Function purity
            r'\bpayable.*function',  # Payable function security
            r'\btry\s*{.*catch',  # Exception handling
        ]
        
        # Initialize lightweight TA agent (simplified)
        self.ta_agent = None
        self._init_lightweight_ta()
        
        # Performance tracking
        self.invocation_stats = {
            'total_checks': 0,
            'ta_invocations': 0,
            'avg_overhead_ms': 0,
            'accuracy_improvements': []
        }
    
    def _init_lightweight_ta(self):
        """Initialize lightweight TA agent for pattern checking"""
        
        try:
            # Simplified TA configuration for speed
            ta_config = ChatGPTConfig(
                temperature=0.1,  # Low temperature for consistency
                max_tokens=500,   # Limited response for speed
                model_type="gpt-4o-mini",  # Fast, cheap model
            )
            
            # Create lightweight TA agent
            ta_system_message = BaseMessage(
                role_name="Technical_Analyst",
                role_type="assistant",  
                meta_dict={},
                content="""You are a lightweight Technical Analyst for smart contract security.
                
Focus ONLY on detecting these critical security patterns:
1. Access control vulnerabilities
2. Reentrancy attack vectors  
3. Integer overflow/underflow risks
4. Improper error handling
5. Unsafe external calls

Provide CONCISE analysis in under 100 words. Format as:
CRITICAL: [list critical issues found]
HIGH: [list high-severity issues]
CONFIDENCE: [0.0-1.0 confidence score]"""
            )
            
            self.ta_agent = ChatAgent(ta_system_message, ta_config)
            logger.info("Lightweight TA agent initialized successfully")
            
        except Exception as e:
            logger.warning(f"Failed to initialize TA agent: {e}. Using pattern-only analysis.")
            self.ta_agent = None
    
    def should_invoke_ta(self, 
                        contract_features: ContractFeatures,
                        current_budget: float,
                        ba_results: Dict[str, Any]) -> EnsembleDecision:
        """
        Determine if TA should be invoked based on complexity and budget
        
        Args:
            contract_features: Analyzed contract features
            current_budget: Remaining budget in USD
            ba_results: Results from Business Analyst mode
        
        Returns:
            EnsembleDecision with invocation recommendation
        """
        
        start_time = time.time()
        self.invocation_stats['total_checks'] += 1
        
        # Calculate contract complexity score (0-1 normalized)
        complexity_score = self._calculate_normalized_complexity(contract_features)
        
        # Estimate TA cost (lightweight pattern analysis)
        estimated_ta_cost = self._estimate_ta_cost(contract_features)
        
        # Check basic thresholds
        meets_complexity_threshold = complexity_score > self.config.complexity_threshold
        meets_budget_threshold = current_budget > self.config.budget_threshold
        has_sufficient_budget = current_budget > estimated_ta_cost
        
        # Additional decision factors
        ba_confidence = ba_results.get('confidence', 0.5)
        ba_found_issues = len(ba_results.get('vulnerabilities', []))
        low_ba_confidence = ba_confidence < self.config.confidence_boost_threshold
        
        # Decision logic
        should_invoke = False
        reason = ""
        confidence = 0.5
        
        if not meets_complexity_threshold:
            reason = f"Contract complexity ({complexity_score:.2f}) below threshold ({self.config.complexity_threshold})"
            confidence = 0.9  # High confidence in not invoking
            
        elif not meets_budget_threshold:
            reason = f"Insufficient budget ({current_budget:.2f} < {self.config.budget_threshold})"
            confidence = 0.9
            
        elif not has_sufficient_budget:
            reason = f"Budget ({current_budget:.2f}) insufficient for estimated TA cost ({estimated_ta_cost:.2f})"
            confidence = 0.8
            
        else:
            # Should invoke TA
            should_invoke = True
            confidence = 0.7 + (complexity_score - self.config.complexity_threshold) * 0.3
            
            if low_ba_confidence:
                reason = f"High complexity ({complexity_score:.2f}) + low BA confidence ({ba_confidence:.2f})"
                confidence += 0.1
            else:
                reason = f"High complexity ({complexity_score:.2f}) warrants TA verification"
        
        # Track performance
        overhead_ms = (time.time() - start_time) * 1000
        self.invocation_stats['avg_overhead_ms'] = (
            (self.invocation_stats['avg_overhead_ms'] * (self.invocation_stats['total_checks'] - 1) + overhead_ms) 
            / self.invocation_stats['total_checks']
        )
        
        if should_invoke:
            self.invocation_stats['ta_invocations'] += 1
        
        decision = EnsembleDecision(
            should_invoke_ta=should_invoke,
            reason=reason,
            estimated_cost=estimated_ta_cost,
            confidence=confidence,
            complexity_score=complexity_score,
            budget_remaining=current_budget - estimated_ta_cost if should_invoke else current_budget
        )
        
        logger.debug(f"Ensemble decision: {should_invoke} ({reason})")
        return decision
    
    def _calculate_normalized_complexity(self, features: ContractFeatures) -> float:
        """Calculate normalized complexity score (0-1 scale)"""
        
        # Base complexity factors
        complexity = 0.0
        
        # Code size (0-0.3 weight)
        loc_factor = min(1.0, features.lines_of_code / 1000.0)
        complexity += loc_factor * 0.3
        
        # Function complexity (0-0.25 weight)
        func_factor = min(1.0, features.function_count / 50.0)
        complexity += func_factor * 0.25
        
        # Control flow complexity (0-0.2 weight)
        control_factor = min(1.0, (features.loop_count + features.conditional_count) / 20.0)
        complexity += control_factor * 0.2
        
        # Security complexity (0-0.25 weight)
        security_factors = sum([
            features.uses_assembly,
            features.uses_delegatecall,
            features.has_payable_functions,
            features.external_calls > 5,
            features.inheritance_depth > 2
        ])
        security_factor = min(1.0, security_factors / 5.0)
        complexity += security_factor * 0.25
        
        return min(1.0, complexity)  # Cap at 1.0
    
    def _estimate_ta_cost(self, features: ContractFeatures) -> float:
        """Estimate cost of lightweight TA analysis"""
        
        # Base cost for pattern analysis
        base_cost = 1.0
        
        # Scale with contract size (lightweight analysis is fast)
        size_multiplier = 1.0 + (features.lines_of_code / 2000.0)  # Very modest scaling
        
        # Function complexity adds minimal cost
        func_multiplier = 1.0 + (features.function_count / 100.0)
        
        estimated_cost = base_cost * size_multiplier * func_multiplier
        return min(estimated_cost, 5.0)  # Cap at $5 for lightweight analysis
    
    async def invoke_lightweight_ta(self, contract_code: str) -> Dict[str, Any]:
        """
        Perform lightweight TA analysis focusing on critical patterns
        
        Args:
            contract_code: Solidity contract source code
        
        Returns:
            Lightweight TA results
        """
        
        start_time = time.time()
        
        try:
            # Pattern-based analysis (always available)
            pattern_results = self._analyze_critical_patterns(contract_code)
            
            # LLM-based analysis (if available)
            llm_results = None
            if self.ta_agent:
                try:
                    llm_results = await self._llm_pattern_analysis(contract_code)
                except Exception as e:
                    logger.warning(f"LLM TA analysis failed: {e}")
            
            # Combine results
            ta_results = {
                'analysis_type': 'lightweight_ta',
                'execution_time': time.time() - start_time,
                'pattern_analysis': pattern_results,
                'llm_analysis': llm_results,
                'total_issues_found': len(pattern_results.get('critical', [])) + len(pattern_results.get('high', [])),
                'confidence': self._calculate_ta_confidence(pattern_results, llm_results)
            }
            
            logger.info(f"Lightweight TA completed in {ta_results['execution_time']:.2f}s, "
                       f"found {ta_results['total_issues_found']} issues")
            
            return ta_results
            
        except Exception as e:
            logger.error(f"Lightweight TA analysis failed: {e}")
            return {
                'analysis_type': 'lightweight_ta',
                'execution_time': time.time() - start_time,
                'error': str(e),
                'total_issues_found': 0,
                'confidence': 0.0
            }
    
    def _analyze_critical_patterns(self, contract_code: str) -> Dict[str, List[str]]:
        """Analyze contract for critical security patterns"""
        
        critical_issues = []
        high_issues = []
        
        # Check for missing access controls
        if not re.search(r'\bonlyOwner\b|\bAccessControl\b|\brequire\s*\(\s*msg\.sender', contract_code, re.IGNORECASE):
            critical_issues.append("Missing access control mechanisms")
        
        # Check for unsafe external calls
        unsafe_call_patterns = [
            r'\.call\s*\(',
            r'\.delegatecall\s*\(',
            r'\.send\s*\(',
        ]
        for pattern in unsafe_call_patterns:
            if re.search(pattern, contract_code, re.IGNORECASE):
                high_issues.append(f"Potentially unsafe external call pattern: {pattern}")
        
        # Check for reentrancy protection
        payable_funcs = re.findall(r'function\s+\w+.*payable', contract_code, re.IGNORECASE)
        if payable_funcs and not re.search(r'ReentrancyGuard|nonReentrant', contract_code, re.IGNORECASE):
            critical_issues.append("Payable functions without reentrancy protection")
        
        # Check for proper error handling
        if not re.search(r'\brequire\s*\(|\brevert\s*\(|\bassert\s*\(', contract_code, re.IGNORECASE):
            high_issues.append("Missing error handling mechanisms")
        
        # Check for integer overflow protection
        if not re.search(r'SafeMath|unchecked\s*\{', contract_code, re.IGNORECASE):
            arithmetic_ops = len(re.findall(r'[+\-*/]\s*=|\+\+|--', contract_code))
            if arithmetic_ops > 3:
                high_issues.append("Arithmetic operations without overflow protection")
        
        return {
            'critical': critical_issues,
            'high': high_issues
        }
    
    async def _llm_pattern_analysis(self, contract_code: str) -> Dict[str, Any]:
        """Use LLM for advanced pattern analysis"""
        
        if not self.ta_agent:
            return None
        
        # Truncate code for lightweight analysis
        max_code_length = 2000  # Limit for speed
        if len(contract_code) > max_code_length:
            contract_code = contract_code[:max_code_length] + "\n// ... [truncated for analysis]"
        
        # Create analysis prompt
        analysis_prompt = f"""
Analyze this Solidity contract for CRITICAL security issues only:

```solidity
{contract_code}
```

Focus on: access control, reentrancy, integer overflow, unsafe calls, error handling.
Respond in exactly this format:
CRITICAL: [comma-separated list or "None"]
HIGH: [comma-separated list or "None"] 
CONFIDENCE: [0.0-1.0]
"""
        
        try:
            # Send to TA agent with timeout
            response = await asyncio.wait_for(
                self.ta_agent.step(BaseMessage(role_name="user", role_type="user", content=analysis_prompt)),
                timeout=self.config.ta_timeout
            )
            
            # Parse response
            content = response.content
            
            critical_match = re.search(r'CRITICAL:\s*(.+)', content, re.IGNORECASE)
            high_match = re.search(r'HIGH:\s*(.+)', content, re.IGNORECASE)
            confidence_match = re.search(r'CONFIDENCE:\s*([0-9.]+)', content, re.IGNORECASE)
            
            critical_issues = []
            high_issues = []
            
            if critical_match and critical_match.group(1).strip().lower() != "none":
                critical_issues = [issue.strip() for issue in critical_match.group(1).split(',')]
            
            if high_match and high_match.group(1).strip().lower() != "none":
                high_issues = [issue.strip() for issue in high_match.group(1).split(',')]
            
            confidence = float(confidence_match.group(1)) if confidence_match else 0.5
            
            return {
                'critical': critical_issues,
                'high': high_issues,
                'confidence': confidence,
                'raw_response': content[:200] + "..." if len(content) > 200 else content
            }
            
        except Exception as e:
            logger.warning(f"LLM TA analysis error: {e}")
            return None
    
    def _calculate_ta_confidence(self, pattern_results: Dict, llm_results: Optional[Dict]) -> float:
        """Calculate overall confidence in TA analysis"""
        
        pattern_confidence = 0.7  # Base confidence in pattern matching
        
        # Boost confidence based on issues found
        total_issues = len(pattern_results.get('critical', [])) + len(pattern_results.get('high', []))
        if total_issues > 0:
            pattern_confidence += min(0.2, total_issues * 0.05)
        
        # Incorporate LLM confidence if available
        if llm_results and 'confidence' in llm_results:
            llm_confidence = llm_results['confidence']
            # Weighted average
            overall_confidence = (pattern_confidence * 0.6) + (llm_confidence * 0.4)
        else:
            overall_confidence = pattern_confidence
        
        return min(1.0, overall_confidence)
    
    def merge_results(self, 
                     ba_results: Dict[str, Any], 
                     ta_results: Dict[str, Any]) -> Dict[str, Any]:
        """
        Merge BA and TA results using ensemble strategy
        
        Args:
            ba_results: Business Analyst mode results
            ta_results: Technical Analysis mode results
        
        Returns:
            Merged ensemble results
        """
        
        if self.config.merge_strategy == "weighted_union":
            return self._weighted_union_merge(ba_results, ta_results)
        elif self.config.merge_strategy == "intersection":
            return self._intersection_merge(ba_results, ta_results)
        else:  # union
            return self._union_merge(ba_results, ta_results)
    
    def _weighted_union_merge(self, ba_results: Dict, ta_results: Dict) -> Dict[str, Any]:
        """Merge using weighted union of findings"""
        
        # Extract vulnerabilities from both analyses
        ba_vulns = ba_results.get('vulnerabilities', [])
        ta_vulns = []
        
        # Convert TA pattern results to vulnerability format
        if 'pattern_analysis' in ta_results:
            pattern_analysis = ta_results['pattern_analysis']
            
            for issue in pattern_analysis.get('critical', []):
                ta_vulns.append({
                    'type': 'critical',
                    'description': issue,
                    'source': 'TA_pattern',
                    'confidence': 0.8
                })
            
            for issue in pattern_analysis.get('high', []):
                ta_vulns.append({
                    'type': 'high', 
                    'description': issue,
                    'source': 'TA_pattern',
                    'confidence': 0.7
                })
        
        # Add LLM findings if available
        if ta_results.get('llm_analysis'):
            llm_analysis = ta_results['llm_analysis']
            
            for issue in llm_analysis.get('critical', []):
                ta_vulns.append({
                    'type': 'critical',
                    'description': issue,
                    'source': 'TA_llm',
                    'confidence': llm_analysis.get('confidence', 0.7)
                })
            
            for issue in llm_analysis.get('high', []):
                ta_vulns.append({
                    'type': 'high',
                    'description': issue,
                    'source': 'TA_llm', 
                    'confidence': llm_analysis.get('confidence', 0.7)
                })
        
        # Merge and deduplicate vulnerabilities
        all_vulns = []
        
        # Add BA vulnerabilities with BA weight
        for vuln in ba_vulns:
            vuln_copy = vuln.copy()
            vuln_copy['ensemble_weight'] = self.config.ba_weight
            vuln_copy['source'] = vuln.get('source', 'BA')
            all_vulns.append(vuln_copy)
        
        # Add unique TA vulnerabilities with TA weight
        for ta_vuln in ta_vulns:
            # Simple deduplication based on description similarity
            is_duplicate = False
            for existing_vuln in all_vulns:
                if self._are_similar_vulnerabilities(ta_vuln, existing_vuln):
                    # Boost confidence of existing finding
                    existing_vuln['confidence'] = min(1.0, existing_vuln['confidence'] + 0.1)
                    existing_vuln['confirmed_by_ta'] = True
                    is_duplicate = True
                    break
            
            if not is_duplicate:
                ta_vuln['ensemble_weight'] = self.config.ta_weight
                all_vulns.append(ta_vuln)
        
        # Calculate ensemble confidence
        ensemble_confidence = self._calculate_ensemble_confidence(ba_results, ta_results)
        
        # Construct merged results
        merged_results = {
            'analysis_mode': 'ensemble_ba_ta',
            'ba_confidence': ba_results.get('confidence', 0.5),
            'ta_confidence': ta_results.get('confidence', 0.5),
            'ensemble_confidence': ensemble_confidence,
            'vulnerabilities': all_vulns,
            'total_vulnerabilities': len(all_vulns),
            'ba_contribution': len(ba_vulns),
            'ta_contribution': len([v for v in all_vulns if v.get('source', '').startswith('TA')]),
            'execution_time': ba_results.get('execution_time', 0) + ta_results.get('execution_time', 0),
            'cost_estimate': ba_results.get('cost_estimate', 0) + ta_results.get('cost_estimate', 0),
            'ensemble_strategy': self.config.merge_strategy
        }
        
        return merged_results
    
    def _are_similar_vulnerabilities(self, vuln1: Dict, vuln2: Dict) -> bool:
        """Check if two vulnerabilities are similar (simple string matching)"""
        
        desc1 = vuln1.get('description', '').lower()
        desc2 = vuln2.get('description', '').lower()
        
        # Check for common keywords
        common_words = set(desc1.split()) & set(desc2.split())
        return len(common_words) >= 2  # At least 2 words in common
    
    def _calculate_ensemble_confidence(self, ba_results: Dict, ta_results: Dict) -> float:
        """Calculate overall ensemble confidence"""
        
        ba_conf = ba_results.get('confidence', 0.5)
        ta_conf = ta_results.get('confidence', 0.5)
        
        # Weighted average with slight boost for agreement
        base_confidence = (ba_conf * self.config.ba_weight) + (ta_conf * self.config.ta_weight)
        
        # Boost if both modes found issues (agreement)
        ba_found_issues = len(ba_results.get('vulnerabilities', []))
        ta_found_issues = ta_results.get('total_issues_found', 0)
        
        if ba_found_issues > 0 and ta_found_issues > 0:
            agreement_boost = 0.1
        elif ba_found_issues == 0 and ta_found_issues == 0:
            agreement_boost = 0.05  # Smaller boost for agreeing on no issues
        else:
            agreement_boost = 0.0
        
        return min(1.0, base_confidence + agreement_boost)
    
    def _union_merge(self, ba_results: Dict, ta_results: Dict) -> Dict[str, Any]:
        """Simple union merge - combine all findings"""
        # Simplified implementation for now
        return self._weighted_union_merge(ba_results, ta_results)
    
    def _intersection_merge(self, ba_results: Dict, ta_results: Dict) -> Dict[str, Any]:
        """Intersection merge - only keep findings confirmed by both"""
        # Simplified implementation for now  
        return self._weighted_union_merge(ba_results, ta_results)
    
    def get_ensemble_stats(self) -> Dict[str, Any]:
        """Get ensemble system performance statistics"""
        
        if self.invocation_stats['total_checks'] == 0:
            return {'status': 'no_checks_performed'}
        
        invocation_rate = (self.invocation_stats['ta_invocations'] / 
                         self.invocation_stats['total_checks']) * 100
        
        return {
            'total_checks': self.invocation_stats['total_checks'],
            'ta_invocations': self.invocation_stats['ta_invocations'],
            'invocation_rate_percent': round(invocation_rate, 1),
            'avg_overhead_ms': round(self.invocation_stats['avg_overhead_ms'], 2),
            'accuracy_improvements': self.invocation_stats['accuracy_improvements']
        }

# Example usage and testing
if __name__ == "__main__":
    import asyncio
    
    async def test_ensemble_checker():
        """Test the ensemble checker functionality"""
        
        # Initialize ensemble checker
        ensemble = EnsembleChecker()
        
        # Mock contract features (high complexity)
        high_complexity_features = ContractFeatures(
            lines_of_code=800,
            function_count=25,
            external_calls=8,
            loop_count=5,
            conditional_count=20,
            uses_assembly=True,
            uses_delegatecall=True,
            has_payable_functions=True,
            inheritance_depth=4
        )
        
        # Mock BA results (low confidence)
        ba_results = {
            'confidence': 0.6,
            'vulnerabilities': [
                {'type': 'medium', 'description': 'Potential reentrancy', 'confidence': 0.5}
            ],
            'execution_time': 2.5,
            'cost_estimate': 12.0
        }
        
        # Test decision making
        print("Testing ensemble decision making...")
        decision = ensemble.should_invoke_ta(
            contract_features=high_complexity_features,
            current_budget=20.0,  # Sufficient budget
            ba_results=ba_results
        )
        
        print(f"Should invoke TA: {decision.should_invoke_ta}")
        print(f"Reason: {decision.reason}")
        print(f"Complexity score: {decision.complexity_score:.3f}")
        print(f"Estimated cost: ${decision.estimated_cost:.2f}")
        
        # Test TA invocation if recommended
        if decision.should_invoke_ta:
            print("\nTesting lightweight TA analysis...")
            
            # Mock Solidity contract
            test_contract = '''
            pragma solidity ^0.8.0;
            
            contract TestContract {
                mapping(address => uint256) public balances;
                
                function withdraw(uint256 amount) public {
                    require(balances[msg.sender] >= amount);
                    balances[msg.sender] -= amount;
                    payable(msg.sender).transfer(amount);  // Potential reentrancy
                }
            }
            '''
            
            ta_results = await ensemble.invoke_lightweight_ta(test_contract)
            print(f"TA analysis completed in {ta_results['execution_time']:.2f}s")
            print(f"Issues found: {ta_results['total_issues_found']}")
            print(f"Confidence: {ta_results['confidence']:.3f}")
            
            # Test result merging
            print("\nTesting result merging...")
            merged = ensemble.merge_results(ba_results, ta_results)
            print(f"Ensemble confidence: {merged['ensemble_confidence']:.3f}")
            print(f"Total vulnerabilities: {merged['total_vulnerabilities']}")
            print(f"BA contribution: {merged['ba_contribution']}")
            print(f"TA contribution: {merged['ta_contribution']}")
        
        # Show stats
        print("\nEnsemble statistics:")
        stats = ensemble.get_ensemble_stats()
        print(f"Total checks: {stats['total_checks']}")
        print(f"TA invocation rate: {stats['invocation_rate_percent']}%")
        print(f"Average overhead: {stats['avg_overhead_ms']:.1f}ms")
    
    # Run test
    asyncio.run(test_ensemble_checker())