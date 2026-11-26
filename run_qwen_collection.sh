#!/bin/sh
# Qwen agents (second LLM family): replies for the 269 training contracts and the
# 96 held-out real contracts. Resumable; rerun after a stop or a daily limit.
cd "$(dirname "$0")"
export PYTHONIOENCODING=utf-8
for v in qwen_ba qwen_think qwen_dasp; do
  for d in combined_tools real_tools; do
    py -u rl_tools/collect_llm.py --variant $v --data datasets/tool_results/$d.json --workers 13
  done
done
