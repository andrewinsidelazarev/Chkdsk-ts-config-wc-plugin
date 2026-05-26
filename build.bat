@echo off
rem Build ChkDsk FAT32 plugin and inject into a Wild Commander wc.img.
rem Usage: build.bat [path\to\wc.img]   (default: Desktop\unreal_x64\wc.img)
setlocal
cd /d "%~dp0"

set IMG=%~1
if "%IMG%"=="" set IMG=C:\Users\Администратор\Desktop\Unreal\wc.img

echo === assemble ===
pushd src
c:\z80\zuma\sjasmplus.exe --sym=dbg.sym main.a80
if errorlevel 1 (echo BUILD FAILED & popd & exit /b 1)
popd

echo === inject into wc.img ===
python inject_chkdsk_to_wc_img.py --img "%IMG%"
if errorlevel 1 (echo INJECT FAILED & exit /b 1)

echo DONE - launch WC, open the plugin menu, run ChkDsk FAT32
