@echo off
REM Daily unattended pipeline (task "SCA TA collect", 21:00). Every step is resumable and
REM stops cleanly at the Groq daily limit; rerun any time.
REM  1. base TA mode on the 269-contract dataset, then its comparison with PPO
REM  2. the 3 LLM calls for the 179 real SmartBugs contracts
REM  3. finish_pipeline: TA-as-tools PPO run and real-only PPO run, once each when ready
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
echo ===== %date% %time% ===== >> verify\results\ta_collection.log
py -u rl_tools\collect_ta.py --max-minutes 100 >>verify\results\ta_collection.log 2>&1
py -u rl_tools\score_ta.py >> verify\results\ta_collection.log 2>&1
if exist datasets\tool_results\real_tools.json (
  py -u rl_tools\collect_llm.py --data datasets\tool_results\real_tools.json --variant ba >> verify\results\ta_collection.log 2>&1
  py -u rl_tools\collect_llm.py --data datasets\tool_results\real_tools.json --variant ba_med >> verify\results\ta_collection.log 2>&1
  py -u rl_tools\collect_llm.py --data datasets\tool_results\real_tools.json --variant ba_dasp >> verify\results\ta_collection.log 2>&1
)
py -u rl_tools\finish_pipeline.py >> verify\results\ta_collection.log 2>&1
