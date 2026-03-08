mkdir myclaw && cd myclaw
# copy all files from above

python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

python onboard.py
# edit ~/.myclaw/config.json with your Telegram token + user ID

# Start local Ollama
ollama run llama3.2

# Test in console
python cli.py agent

# Or Telegram gateway
python cli.py gateway