# How to Run MyClaw

## Setup

```bash
git clone https://github.com/adrianx26/zensynora.git
cd zensynora

# Create & activate virtual environment
python -m venv venv
source venv/bin/activate        # Linux / macOS
# venv\Scripts\activate         # Windows

pip install -r requirements.txt

# Run the onboarding wizard
python onboard.py
```

Edit `~/.myclaw/config.json` to configure:
- **Telegram** — bot token + your user ID
- **WhatsApp** — `phone_number_id`, `access_token`, `verify_token` (see `plans/whatsapp_implementation_plan.md`)
- **Provider** — Ollama, OpenAI, Anthropic, Gemini, or other supported LLM provider

## Running

### Console Mode

```bash
python cli.py agent
```

### Telegram Gateway

```bash
# Ensure your chosen provider is running (e.g. Ollama)
ollama run llama3.2

# Start the Telegram bot
python cli.py gateway
```

### WhatsApp Gateway

```bash
# Ensure WhatsApp is enabled in config and provider is running
ollama run llama3.2

# Expose a public webhook URL (for development use ngrok)
ngrok http 8000

# Start the gateway (starts both Telegram and WhatsApp if enabled)
python cli.py gateway
```

## Linux Auto-Start (systemd)

Use `install.sh` to optionally set up a systemd service that auto-starts the Telegram gateway on boot:

```bash
chmod +x install.sh
./install.sh
```