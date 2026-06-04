@echo off
REM ============================================================
REM  BOM 对比工具 - 启动脚本（双击运行）
REM  自动定位到 BAT 文件所在目录，无需修改路径
REM ============================================================

cd /d "%~dp0"

REM --- 检查 Python 是否可用 ---
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Python，请先安装 Python 并添加到 PATH。
    pause
    exit /b 1
)

REM --- 检查虚拟环境，不存在则自动创建 ---
if not exist "venv\" (
    echo [初始化] 首次运行，正在创建虚拟环境...
    python -m venv venv
    if errorlevel 1 (
        echo [错误] 创建虚拟环境失败。
        pause
        exit /b 1
    )
    echo [初始化] 正在安装依赖包（可能需要几分钟）...
    call venv\Scripts\activate.bat
    pip install -r requirements.txt
    if errorlevel 1 (
        echo [错误] 安装依赖失败，请检查 requirements.txt。
        pause
        exit /b 1
    )
    echo [完成] 环境初始化完成！
)

REM --- 激活虚拟环境 ---
call venv\Scripts\activate.bat

REM --- 检查关键依赖是否已安装 ---
python -c "import flask" >nul 2>&1
if errorlevel 1 (
    echo [提示] 检测到依赖未安装，正在安装...
    pip install -r requirements.txt
)

echo.
echo ============================================================
echo   BOM 对比工具 启动中...
echo   访问地址: http://localhost:5002
echo   按 Ctrl+C 停止服务
echo ============================================================
echo.

REM --- 启动 Flask ---
python run.py

pause
