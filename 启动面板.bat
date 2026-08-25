@echo off
chcp 65001 >nul
REM 灵梦work Web 控制台启动器 (Windows)
REM 双击此文件即用打包好的 lingmengwork.exe 起 Web 面板, 并自动打开浏览器
setlocal EnableDelayedExpansion
cd /d "%~dp0"

REM 启动前加载 .env (若不存在则用系统环境变量; .env 请勿提交进版本库)
if exist ".env" (
  for /f "usebackq tokens=1,* delims==" %%a in ("%CD%\.env") do (
    if not "%%a"=="" if not "%%a:~0,1%"=="#" (
      set "%%a=%%b"
    )
  )
  echo [env] 已从 .env 载入 API Key 等环境变量
)

set EXE=dist\lingmengwork\lingmengwork.exe
if not exist "%EXE%" (
  echo [错误] 未找到 %EXE%
  echo 请先运行打包, 或改用源码模式: python lingmengwork_launcher.py web
  pause
  exit /b 1
)

REM 默认端口
set PORT=8318

echo 灵梦work Web 控制台
echo   [1] 本机访问 (127.0.0.1)
echo   [2] 局域网访问 (手机/安卓同网可连, 0.0.0.0)
set /p MODE=选择模式 [1/2, 默认1]:

if "%MODE%"=="2" (
  set HOST=0.0.0.0
) else (
  set HOST=127.0.0.1
)

REM 先释放目标端口, 避免上一次残留进程占用导致打不开
powershell -NoProfile -Command "try { $c = Get-NetTCPConnection -LocalPort %PORT% -ErrorAction SilentlyContinue; if ($c) { Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue; Write-Host '已释放端口 %PORT%' } } catch {}" >nul 2>&1
timeout /t 1 >nul

echo.
echo 正在启动 Web 面板 (端口 %PORT%) ...
echo 启动后将自动打开浏览器, 地址: http://%HOST%:%PORT%
echo (若浏览器未自动打开, 请手动访问上面的地址)
echo.

REM 用 start 在独立窗口启动 exe, 即使报错也不会一闪而过
start "灵梦work Web" cmd /k "%EXE% web --host %HOST% --port %PORT%"

REM 等待 server 起来再开浏览器
set /a waits=0
:waitloop
timeout /t 1 >nul
powershell -NoProfile -Command "try { $r = Invoke-WebRequest -Uri 'http://127.0.0.1:%PORT%/api/health' -UseBasicParsing -TimeoutSec 1; if ($r.StatusCode -eq 200) { exit 0 } else { exit 1 } } catch { exit 1 }" >nul 2>&1
if %errorlevel%==0 (
  start "" "http://%HOST%:%PORT%/"
  goto :done
)
set /a waits+=1
if %waits% lss 15 goto :waitloop
echo [警告] 15 秒内未检测到服务, 请检查上面的窗口是否有报错。

:done
endlocal
