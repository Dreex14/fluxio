@echo off
setlocal
cd /d "%~dp0"

rem ============================================================
rem  Genera dist\Fluxio.exe  (portable, sin Python)
rem
rem  INCLUIR_FFMPEG=1  -> ffmpeg va dentro del .exe (mas comodo, pesa mas)
rem  INCLUIR_FFMPEG=0  -> el .exe es mas ligero; se copia dist\ffmpeg.exe aparte
rem ============================================================
set INCLUIR_FFMPEG=1

set "FFMPEG_OPT="
if "%INCLUIR_FFMPEG%"=="1" set "FFMPEG_OPT=--collect-all imageio_ffmpeg"

echo === Instalando dependencias y PyInstaller ===
python -m pip install -r requirements.txt pyinstaller || goto :err

echo.
echo === Compilando (tarda unos minutos) ===
python -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name "Fluxio" ^
  --icon icon.ico ^
  --add-data "icon.ico;." ^
  --collect-submodules yt_dlp ^
  --copy-metadata yt-dlp ^
  %FFMPEG_OPT% ^
  main.pyw || goto :err

if not "%INCLUIR_FFMPEG%"=="1" python -c "import imageio_ffmpeg,shutil; shutil.copy(imageio_ffmpeg.get_ffmpeg_exe(), r'dist\ffmpeg.exe')"
echo.
echo Listo: dist\Fluxio.exe
if not "%INCLUIR_FFMPEG%"=="1" echo        y dist\ffmpeg.exe (dejalos juntos en la misma carpeta)
pause
exit /b 0

:err
echo.
echo Algo ha fallado. Revisa los mensajes de arriba.
pause
exit /b 1
