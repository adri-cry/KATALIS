@echo off
rem KATALIS - jalankan dashboard.
rem
rem Dua jebakan Windows yang ditangani di sini:
rem   1. "App Execution Alias" Microsoft Store menangkap perintah "python" dan
rem      hanya menampilkan "Python was not found; run without arguments to
rem      install from the Microsoft Store", walau Python terpasang.
rem   2. "py -3" kadang menunjuk ke core interpreter (pythoncore-3.x-64) yang
rem      tidak bisa dijalankan langsung.
rem Karena itu setiap kandidat diuji dengan benar-benar menjalankan Python,
rem bukan sekadar dilihat keberadaannya.
setlocal EnableDelayedExpansion
cd /d "%~dp0"

set "PY="

rem --- kandidat, berurutan dari yang paling spesifik ------------------------
if exist ".venv\Scripts\python.exe" call :try "%CD%\.venv\Scripts\python.exe"

rem launcher resmi "py": tanya lokasi python-nya, lalu uji path itu
for /f "delims=" %%P in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do call :try "%%P"

rem setiap python.exe di PATH (stub Store akan gagal di sini)
for /f "delims=" %%P in ('where python 2^>nul') do call :try "%%P"
for /f "delims=" %%P in ('where python3 2^>nul') do call :try "%%P"

rem lokasi pemasangan yang umum
call :try "%LOCALAPPDATA%\Python\bin\python.exe"
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do call :try "%%~fD\python.exe"
for /d %%D in ("%PROGRAMFILES%\Python3*") do call :try "%%~fD\python.exe"

if not defined PY goto :nopython

rem --- versi: cukup 3.10+; kegagalan membaca versi tidak mematikan program ---
rem Nilai ditulis ke berkas sementara: kutip bersarang di dalam "for /f" membuat
rem perintah dengan path berspasi gagal tanpa pesan apa pun.
set "PYVER="
"%PY%" -c "import sys; print(sys.version_info[0]*100+sys.version_info[1])" > "%TEMP%\katalis_pyver.txt" 2>nul
if exist "%TEMP%\katalis_pyver.txt" (
  for /f "usebackq delims=" %%V in ("%TEMP%\katalis_pyver.txt") do set "PYVER=%%V"
  del "%TEMP%\katalis_pyver.txt" >nul 2>nul
)
if defined PYVER if !PYVER! LSS 310 goto :oldpython

echo.
echo   KATALIS dijalankan dengan:
echo   %PY%
echo.
"%PY%" app.py %*
set "CODE=%ERRORLEVEL%"
echo.
if not "%CODE%"=="0" (
  echo   app.py berhenti dengan kode %CODE% - pesan di atas menjelaskan sebabnya.
) else (
  echo   Server berhenti.
)
echo   Tekan tombol apa saja untuk menutup jendela ini.
pause >nul
exit /b %CODE%

rem --- subrutin: pakai kandidat hanya bila benar-benar menjalankan Python ---
:try
if defined PY exit /b 0
if "%~1"=="" exit /b 0
if not exist "%~1" exit /b 0
"%~1" -c "import sys" >nul 2>nul || exit /b 0
set "PY=%~1"
exit /b 0

:oldpython
echo.
echo   Python yang ditemukan terlalu tua (kode versi !PYVER!).
echo   KATALIS butuh Python 3.10 atau lebih baru.
echo   Lokasi: %PY%
echo.
pause
exit /b 1

:nopython
echo.
echo   Python tidak ditemukan oleh skrip ini.
echo.
echo   Bila Anda yakin sudah memasang Python 3.10+ (misalnya 3.14), kemungkinan
echo   besar perintah "python" di Windows ditangkap "App Execution Alias"
echo   Microsoft Store - itulah pesan "Python was not found; run without
echo   arguments to install from the Microsoft Store".
echo.
echo   Pilih salah satu perbaikan:
echo.
echo     1. Coba launcher resmi:        py app.py
echo     2. Matikan alias Store:        Settings ^> Apps ^> Advanced app settings
echo                                    ^> App execution aliases
echo                                    ^> matikan "python.exe" dan "python3.exe"
echo     3. Pasang ulang dari python.org dan centang
echo        "Add python.exe to PATH"
echo.
echo   Setelah itu jalankan run.bat lagi.
echo.
pause
exit /b 1
