@echo off
rem Start the Huawei Training Camp session keeper with the same state
rem directory the MCP server uses. Extra arguments are passed through,
rem e.g.  start_session_keeper.cmd --minimized --interval-minutes 15
setlocal
set "ROOT=%~dp0.."
rem Set HTC_STATE_DIR in the user environment when multiple hosts must share it.
rem Otherwise the connector uses its normal LocalAppData default.
set "PYTHONPATH=%ROOT%"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
cd /d "%ROOT%"
"%ROOT%\.venv\Scripts\python.exe" -m scripts.session_keeper %*
endlocal
