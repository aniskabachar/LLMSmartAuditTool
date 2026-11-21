@echo off
REM Tool-selection RL (rl_tools/run.py): trains + evaluates on local data, no internet needed.
REM Safe to rerun: finished folds are kept in verify\results\tools\_checkpoints and skipped.
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
echo ===== %date% %time% ===== >> verify\results\tools_run.log
REM Data: SmartBugs curated (69) + SolidiFI (200), 4 shared static tools + 3 LLM calls
py -u rl_tools\run.py --steps 40000 --data datasets\tool_results\combined_tools.json >> verify\results\tools_run.log 2>&1
