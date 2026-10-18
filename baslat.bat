@echo off
REM Oskilo - kaynak koddan calistirma (Windows)
REM Ilk calistirmada sanal ortam kurar ve kutuphaneleri indirir (1-2 dk).
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" goto kurulum
REM Yeni surumde kutuphane listesi degistiyse sadece eksikleri kur
fc /b requirements.txt ".venv\req.txt" >nul 2>nul
if errorlevel 1 goto guncelle
goto calistir

:guncelle
echo Kutuphaneler guncelleniyor...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo Kutuphaneler guncellenemedi; eski haliyle aciliyor.
    pause
    goto calistir
)
copy /y requirements.txt ".venv\req.txt" >nul
goto calistir

:kurulum

REM --- Gercek bir Python var mi? (Microsoft Store kisayolu sayilmaz) ---
set "PY="
py -3 --version >nul 2>nul && set "PY=py -3"
if not defined PY (
    python --version >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo.
    echo  Bilgisayarda Python kurulu degil.
    echo.
    echo  1^) Acilacak sayfadan Python'u indirip kurun.
    echo  2^) Kurulumun ILK ekraninda "Add python.exe to PATH" kutusunu isaretleyin.
    echo  3^) Kurulum bitince bu dosyaya tekrar cift tiklayin.
    echo.
    start "" "https://www.python.org/downloads/windows/"
    pause
    exit /b 1
)

echo Ilk kurulum yapiliyor, 1-2 dakika surebilir...
if exist ".venv" rmdir /s /q ".venv"
%PY% -m venv .venv
if not exist ".venv\Scripts\python.exe" (
    echo Sanal ortam olusturulamadi.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo Kutuphaneler indirilemedi. Internet baglantisini kontrol edip tekrar deneyin.
    rmdir /s /q ".venv"
    pause
    exit /b 1
)
copy /y requirements.txt ".venv\req.txt" >nul

:calistir
start "" ".venv\Scripts\pythonw.exe" main.py
