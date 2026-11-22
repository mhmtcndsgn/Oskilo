@echo off
REM Oskilo - kart olmadan ag denemesi: sahte Oskilo karti
REM Oskilo onu kendiliginden bulur: Port listesinde "Oskilo ..." olarak gorunur.
REM Ornek: ag_test_karti.bat --name "Deney 1" --mode dma
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" (
    echo Once Oskilo Kaynak klasorundeki baslat.bat dosyasini bir kez calistirin.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" araclar\ag_test_gonderici.py %*
pause
