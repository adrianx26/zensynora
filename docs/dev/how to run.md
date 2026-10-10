# How to Run MyClaw

## Setup

```bash
git clone https://github.com/adrianx26/zensynora.git
cd zensynora

# Create & activate virtual environment
python3 -m venv .venv
source .venv/bin/activate       # Linux / macOS

python -m pip install -r requirements.txt

# Run the onboarding wizard
python onboard.py
```

On Windows PowerShell, use `py -m venv .venv`, activate it with
`.\.venv\Scripts\Activate.ps1`, and then run
`python -m pip install -r requirements.txt`. Keep installs inside the virtual
environment; Debian/Ubuntu system Python is externally managed (PEP 668).

Edit `~/.myclaw/config.json` to configure:
- **Telegram** — bot token + your user ID
- **WhatsApp** — `phone_number_id`, `access_token`, `verify_token` (see `../plans/whatsapp_implementation_plan.md`)
- **Provider** — Ollama, OpenAI, Anthropic, Gemini, or other supported LLM provider

The install routine checks for Python virtual-environment support before
installing packages. On Debian/Ubuntu it offers to install `python3-venv` if
needed, validates any existing `venv/`, and offers to recreate a broken one.
Use `./install.sh --no-docker --recreate-venv` to force recreation. Never work around
PEP 668 by installing into system Python.

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

Use `install.sh` to set up the environment and optionally configure a systemd
service that auto-starts the Telegram gateway on boot:

```bash
chmod +x install.sh
./install.sh
```