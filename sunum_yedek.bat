@echo off
setlocal
cd /d "%~dp0"
title Ankara Hastane Yerlesimi - YEDEK (salt goruntuleme)
echo ============================================================
echo   YEDEK MOD: paket gerektirmez, canli cozum KAPALI.
echo   Sayfa, onceden cozulmus 3 km varsayilan cozumu "cevrimdisi mod" ile gosterir.
echo   Adres: http://127.0.0.1:8001   (kapatmak icin bu pencereyi kapatin)
echo ============================================================
set "PY="
py -3 -c "import sys" >nul 2>&1 && set "PY=py -3" && goto havepy
python -c "import sys" >nul 2>&1 && set "PY=python" && goto havepy
echo [HATA] Python bulunamadi.
pause
exit /b 1
:havepy
start "" http://127.0.0.1:8001/
%PY% -m http.server 8001 --bind 127.0.0.1 --directory backend\static
pause
