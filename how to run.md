mkdir myclaw && cd myclaw
# copiază toate fișierele de mai sus

python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

python onboard.py
# editează ~/.myclaw/config.json cu token-ul tău Telegram + user ID

# Pornește Ollama local
ollama run llama3.2

# Test în consolă
python cli.py agent

# Sau gateway Telegram
python cli.py gateway