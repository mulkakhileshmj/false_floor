$ErrorActionPreference = "Stop"
python (Join-Path $PSScriptRoot "run_panel.py") --models mockllm/model --answer-format letter --max-tokens 64 @args
exit $LASTEXITCODE
