@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
title Ankara Hastane Yerlesimi - sunucu

echo ============================================================
echo   Ankara icin optimum hastane yerlesimi
echo   Sunucu: http://127.0.0.1:8000   (kapatmak icin bu pencereyi kapatin)
echo ============================================================

set "PY="
py -3.14 -c "import sys" >nul 2>&1 && set "PY=py -3.14" && goto havepy
py -3.13 -c "import sys" >nul 2>&1 && set "PY=py -3.13" && goto havepy
py -3.12 -c "import sys" >nul 2>&1 && set "PY=py -3.12" && goto havepy
python -c "import sys; assert sys.version_info >= (3, 12)" >nul 2>&1 && set "PY=python" && goto havepy
echo [HATA] Python 3.12 - 3.14 bulunamadi.
echo        python.org adresinden 64-bit Python 3.14 kurun ve "Add python.exe to PATH" kutusunu isaretleyin.
pause
exit /b 1

:havepy
echo Python: %PY%

if exist ".venv\Scripts\python.exe" goto havevenv
echo Sanal ortam olusturuluyor (.venv) ...
%PY% -m venv .venv
if errorlevel 1 goto venvfail

:havevenv
if exist ".venv\kurulum_tamam.txt" goto run
echo Paketler kuruluyor - ilk seferde 1-3 dakika surer ve INTERNET gerekir ...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements-lock.txt
if errorlevel 1 goto pipfail
echo ok> ".venv\kurulum_tamam.txt"

:run
echo Sunucu baslatiliyor; tarayici birkac saniye icinde acilacak ...
".venv\Scripts\python.exe" -m backend.run %*
echo.
echo Sunucu kapandi. Hata varsa yukaridaki mesajlari okuyun. Yedek: sunum_yedek.bat
pause
exit /b 0

:venvfail
echo [HATA] Sanal ortam olusturulamadi.
pause
exit /b 1

:pipfail
echo [HATA] Paket kurulumu basarisiz. Internet baglantisini kontrol edip start.bat'i yeniden calistirin.
echo        Hemen gostermeniz gerekiyorsa: sunum_yedek.bat (paket gerektirmez, varsayilan cozumu gosterir).
pause
exit /b 1
