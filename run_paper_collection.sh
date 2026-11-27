#!/usr/bin/env bash
# Collect every analyser's replies on the base paper's benchmark (rl_tools/build_paper_dataset.py).
# Resumable: rerun to continue after a daily limit or a shutdown (finished replies are kept).
cd "$(dirname "$0")"
export PYTHONIOENCODING=utf-8
D=datasets/tool_results/paper_tools.json
LOG=verify/results/paper_collection.log
{
  echo "=== $(date) TA mode (base paper) ==="
  py rl_tools/collect_ta.py --data $D
  for v in ba ba_med ba_dasp qwen_ba qwen_think qwen_dasp; do
    echo "=== $(date) $v ==="
    py rl_tools/collect_llm.py --variant $v --data $D
  done
  echo "=== $(date) done ==="
} >> "$LOG" 2>&1
