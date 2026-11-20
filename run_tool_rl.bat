@echo off
REM Tool-selection RL (rl_tools/run.py): trains + evaluates on local data, no internet needed.
REM Safe to rerun: finished folds are kept in verify\results\tools\_checkpoints and skipped.
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
echo ===== %date% %time% ===== >> verify\results\tools_run.log
py -u rl_tools\run.py --steps 40000 >> verify\results\tools_run.log 2>&1
