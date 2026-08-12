@echo off
REM Rimuove il servizio "Hub PC - Servizio Input (UAC/Lock screen)".
REM Va eseguito con tasto destro > "Esegui come amministratore".

echo.
echo === Disinstallazione servizio Hub PC ===
echo.

python hub_service.py stop
python hub_service.py remove

echo.
echo Servizio rimosso. L'app continua a funzionare normalmente usando pynput
echo (mouse/tastiera nel desktop normale), solo senza accesso a UAC/lock screen.
pause
