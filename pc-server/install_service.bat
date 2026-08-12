@echo off
REM Installa il servizio Windows "Hub PC - Servizio Input (UAC/Lock screen)".
REM Va eseguito con tasto destro > "Esegui come amministratore": un servizio
REM Windows richiede privilegi di amministratore per essere registrato.

cd /d "%~dp0"

echo.
echo === Installazione servizio Hub PC ===
echo.

pip install -r requirements.txt
if errorlevel 1 goto errore

python hub_service.py --startup auto install
if errorlevel 1 goto errore

python hub_service.py start
if errorlevel 1 goto errore

echo.
echo Servizio installato e avviato correttamente.
echo Si avviera' automaticamente ad ogni accensione del PC, anche prima del login.
pause
exit /b 0

:errore
echo.
echo Installazione fallita. Verifica di aver lanciato questo file come amministratore.
pause
exit /b 1
