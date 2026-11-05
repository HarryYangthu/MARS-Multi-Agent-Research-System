@echo off
chcp 65001 >nul
rem MARS 一键入口（Windows）：首次双击完成安装，之后双击直接启动 mars-app 并打开浏览器。
setlocal
set "ROOT=%~dp0"
set "PY=%ROOT%runtime\python\python.exe"
if not exist "%PY%" (
  if not exist "%ROOT%runtime" mkdir "%ROOT%runtime"
  tar -xzf "%ROOT%bundle\python.tar.gz" -C "%ROOT%runtime" || (echo 解压内置 Python 失败 & pause & exit /b 1)
)
if not exist "%ROOT%code\mars-deploy\local\paths.json" (
  echo 首次运行：正在离线安装（稍候，仅此一次）...
  "%PY%" -I "%ROOT%code\mars-deploy\install\install.py" --no-check || (echo 安装失败 & pause & exit /b 1)
)
rem With arguments, preserve the mars-core CLI; with no arguments, open the app.
if not "%~1"=="" (
  "%PY%" -I "%ROOT%code\mars-deploy\install\run.py" mars %*
  exit /b
)
echo 正在启动 MARS，浏览器稍后会自动打开 http://127.0.0.1:8020/ （关闭此窗口即停止服务）
"%PY%" -I "%ROOT%code\mars-deploy\install\run.py" app --open
