mkdir myclaw && cd myclaw
# copy all files from above

python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

python onboard.py
# edit ~/.myclaw/config.json with your Telegram token + user ID
# or WhatsApp Business credentials (phone_number_id, access_token, verify_token)

# Start local Ollama
ollama run llama3.2

# Test in console
python cli.py agent

# Or Telegram gateway
python cli.py gateway

# Or WhatsApp gateway (requires WhatsApp enabled in config)
# Make sure to set up a public webhook URL (use ngrok for development)
# ngrok http 8000
python cli.py gateway