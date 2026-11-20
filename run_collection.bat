@echo off
REM Collects real LLM audit outputs for offline RL training (rl_offline/collect.py).
REM Safe to run again any time: it resumes from datasets\cache and stops cleanly
REM when Groq's daily token limit is reached. Run once a day until --status shows all done.
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
py rl_offline\collect.py %*
echo.
py rl_offline\collect.py --status
pause
