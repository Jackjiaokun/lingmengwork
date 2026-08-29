@echo off
chcp 65001 >nul
REM 灵梦work 源码面板启动器 v2 (2026-08-29) — 含 Phase 15~77 全量
REM 双击此文件用源码 python 起 Web 面板并自动打开浏览器
REM v2: 受管 venv 优先 / 端口统一 8318 / 清理僵尸监听(含 pythonw) / 探活 /superagent
setlocal EnableDelayedExpansion
cd /d "%~dp0"

REM 启动前加载 .env (API Key 等, 请勿提交)
if exist ".env" (
  for /f "usebackq tokens=1,* delims==" %%a in ("%CD%\.env") do (
    if not "%%a"=="" if not "%%a:~0,1%"=="#" (
      set "%%a=%%b"
    )
  )
  echo [env] 已从 .env 载入环境变量
)

REM ---- python 选择: 受管 venv 优先, 系统 3.11 兜底 ----
set PY=C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe
set PYSRC=受管 venv (3.13, 含全部依赖)
if not exist "%PY%" (
  set PY=C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe
  set PYSRC=系统 Python 3.11
)
if not exist "%PY%" (
  echo [错误] 未找到可用的 python (受管 venv 与系统 3.11 均缺失)
  pause
  exit /b 1
)
echo [py] %PYSRC%

set PORT=8318
set HOST=127.0.0.1

REM ---- 清理 8318 上的残留监听者 (含 pythonw 僵尸: 只监听不服务) ----
powershell -NoProfile -Command "try { $c = Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue; if ($c) { $c | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { $p = Get-Process -Id $_ -ErrorAction SilentlyContinue; if ($p) { Write-Host ('[清理] 杀掉残留监听 pid=' + $_ + ' (' + $p.ProcessName + ')'); Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue } } } else { Write-Host '端口 %PORT% 空闲' } } catch {}"
timeout /t 1 >nul

echo 灵梦work 源码面板 (含 Phase 77 全量)
echo   端口: %PORT%   地址: http://%HOST%:%PORT%/
echo.

start "灵梦work 源码" cmd /k "%PY%" -m lingmengwork.web.server --host %HOST% --port %PORT%

REM ---- 等待服务起来再开浏览器 (探活 /superagent) ----
set /a waits=0
:waitloop
timeout /t 1 >nul
powershell -NoProfile -Command "try { $r = Invoke-WebRequest -Uri 'http://127.0.0.1:%PORT%/superagent' -UseBasicParsing -TimeoutSec 2; if ($r.StatusCode -eq 200) { exit 0 } else { exit 1 } } catch { exit 1 }" >nul 2>&1
if %errorlevel%==0 (
  echo [ok] 面板已就绪, 打开浏览器...
  start "" "http://%HOST%:%PORT%/superagent"
  goto :done
)
set /a waits+=1
if %waits% lss 20 goto :waitloop
echo [警告] 20 秒内未检测到服务, 请检查上面的窗口是否有报错。
start "" "http://%HOST%:%PORT%/superagent"

:done
endlocal
