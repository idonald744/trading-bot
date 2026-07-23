@echo off
echo Starting AI Trading Bots...

echo.
echo [1/3] Starting Ollama (RAG embeddings)...
start "Ollama" cmd /k "ollama serve"

timeout /t 5

echo [2/3] Starting Crypto Bot...
start "Crypto Bot" cmd /k "cd /d %~dp0trading-system && venv\Scripts\activate && python bot.py crypto"

timeout /t 5

echo [3/3] Starting Stock Bot...
start "Stock Bot" cmd /k "cd /d %~dp0trading-system && venv\Scripts\activate && python bot.py stock"

echo.
echo All processes started in separate windows!
echo Crypto bot runs 24/7
echo Stock bot runs 9:30am-4:00pm EST only
pause
