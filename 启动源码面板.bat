@echo off
chcp 65001 >nul
REM 灵梦work 源码面板启动器 (Windows) — 含 Phase 15~19 全量 (自动化/活动/审计/自愈)
REM 双击此文件用源码 python 起 Web 面板并自动打开浏览器, 进程落在你自己的会话, 宿主可达
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

set PY=C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe
if not exist "%PY%" (
  echo [错误] 未找到 %PY%
  pause
  exit /b 1
)

set PORT=8320
set HOST=127.0.0.1

REM 先释放目标端口, 避免上一次残留进程占用导致打不开
powershell -NoProfile -Command "try { $c = Get-NetTCPConnection -LocalPort %PORT% -ErrorAction SilentlyContinue; if ($c) { Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue; Write-Host '已释放端口 %PORT%' } } catch {}" >nul 2>&1
timeout /t 1 >nul

echo 灵梦work 源码面板 (含 Phase 19 全量)
echo   端口: %PORT%   地址: http://%HOST%:%PORT%/
echo.

start "灵梦work 源码" cmd /k "%PY%" -m lingmengwork.web.server --host %HOST% --port %PORT%

REM 等待 server 起来再开浏览器
set /a waits=0
:waitloop
timeout /t 1 >nul
powershell -NoProfile -Command "try { $r = Invoke-WebRequest -Uri 'http://127.0.0.1:%PORT%/api/meta' -UseBasicParsing -TimeoutSec 1; if ($r.StatusCode -eq 200) { exit 0 } else { exit 1 } } catch { exit 1 }" >nul 2>&1
if %errorlevel%==0 (
  start "" "http://%HOST%:%PORT%/"
  goto :done
)
set /a waits+=1
if %waits% lss 20 goto :waitloop
echo [警告] 20 秒内未检测到服务, 请检查上面的窗口是否有报错。
start "" "http://%HOST%:%PORT%/"

:done
endlocal
