# Multi-agent coordinator experiments (no API calls). Each run resumes from its
# per-fold checkpoint, so rerunning this script after a shutdown continues where it stopped.
#   agents_mlp4    9 LLM agents (6 TA specialists + 3 generalists), 64x64 MLP, 4 compact features
#   agents_mlp28   same, all 28 contract features
#   agents_attn28  same, 8-head self-attention policy, all 28 features
#   hybrid_mlp4    9 LLM agents + 4 static tools, MLP, 4 features
# Run:  powershell -ExecutionPolicy Bypass -File run_agents_experiments.ps1
Set-Location $PSScriptRoot
$env:PYTHONIOENCODING = "utf-8"
$A = "--cost both --lams 0.02 --steps 40000"
$runs = [ordered]@{
  "agents_mlp4"   = "--data datasets\tool_results\agents_tools.json $A";
  "agents_mlp28"  = "--data datasets\tool_results\agents_tools.json $A --features all";
  "agents_attn28" = "--data datasets\tool_results\agents_tools.json $A --features all --policy attn --heads 8 --no-static --jobs 7";
  "hybrid_mlp4"   = "--data datasets\tool_results\agents_hybrid_tools.json $A"
}
foreach ($k in $runs.Keys) {
  Start-Process -FilePath "cmd.exe" -WindowStyle Hidden -WorkingDirectory $PSScriptRoot `
    -ArgumentList "/c py -u rl_tools\run.py $($runs[$k]) > verify\results\$k.log 2>&1"
}
