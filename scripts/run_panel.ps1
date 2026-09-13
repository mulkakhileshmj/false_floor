$ErrorActionPreference = "Stop"
python (Join-Path $PSScriptRoot "run_panel.py") @args
exit $LASTEXITCODE
