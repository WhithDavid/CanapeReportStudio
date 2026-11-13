@echo off
cd /d "%~dp0"
if not exist bin mkdir bin
"%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe" /nologo /platform:x64 /target:exe /out:bin\Asap3ReadOnlyProbe.exe Asap3ReadOnlyProbe.cs
exit /b %errorlevel%
