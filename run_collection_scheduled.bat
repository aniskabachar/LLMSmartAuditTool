@echo off
REM Unattended variant of run_collection.bat for the daily "SCA collect" scheduled task.
REM No pause at the end (a waiting window would block the next day's run);
REM output is appended to collection.log in the project folder instead.
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
echo ===== %date% %time% ===== >> collection.log
py -u rl_offline\collect.py %* >> collection.log 2>&1
py rl_offline\collect.py --status >> collection.log 2>&1
REM once the collection target is met: train + evaluate (curated and all labels), only once
py -u rl_offline\finish_if_ready.py >> collection.log 2>&1
