from myclaw.config import save_config, CONFIG_DIR
from myclaw.provider import SUPPORTED_PROVIDERS

_PROVIDER_HINTS = {
    "ollama":     "Local — Ollama (http://localhost:11434)",
    "lmstudio":   "Local — LM Studio (http://localhost:1234/v1)",
    "llamacpp":   "Local — llama.cpp server (http://localhost:8080/v1)",
    "openai":     "Online — OpenAI (requires api_key)",
    "anthropic":  "Online — Anthropic Claude (requires api_key)",
    "gemini":     "Online — Google Gemini (requires api_key)",
    "groq":       "Online — Groq (requires api_key, very fast)",
    "openrouter": "Online — OpenRouter (requires api_key, 100+ models)",
}


def onboard():
    print("🦞 Welcome to MyClaw Onboard!\n")

    # ── Provider selection ─────────────────────────────────────────────────────
    print("Available LLM providers:")
    for i, p in enumerate(SUPPORTED_PROVIDERS, 1):
        print(f"  {i}. {p:12} — {_PROVIDER_HINTS.get(p, '')}")

    choice = input(
        f"\nChoose provider [1-{len(SUPPORTED_PROVIDERS)}] (default: 1 = ollama): "
    ).strip()

    try:
        provider = SUPPORTED_PROVIDERS[int(choice) - 1] if choice else "ollama"
    except (ValueError, IndexError):
        provider = "ollama"

    # ── Model ─────────────────────────────────────────────────────────────────
    _model_hints = {
        "ollama":     "llama3.2",
        "lmstudio":   "local-model",
        "llamacpp":   "local-model",
        "openai":     "gpt-4o-mini",
        "anthropic":  "claude-3-5-sonnet-20241022",
        "gemini":     "gemini-1.5-flash",
        "groq":       "llama3-70b-8192",
        "openrouter": "openai/gpt-4o-mini",
    }
    default_model = _model_hints.get(provider, "llama3.2")
    model = input(f"Model name (default: {default_model}): ").strip() or default_model

    # ── API key (online only) ──────────────────────────────────────────────────
    api_key = ""
    online = {"openai", "anthropic", "gemini", "groq", "openrouter"}
    if provider in online:
        api_key = input(f"API key for {provider}: ").strip()

    # ── Telegram ──────────────────────────────────────────────────────────────
    tg_token = input("Telegram bot token (leave blank to skip): ").strip()
    tg_user  = input("Your Telegram user ID (leave blank to skip): ").strip()
    tg_enabled = bool(tg_token and tg_user)

    # ── WhatsApp ──────────────────────────────────────────────────────────────
    print("\n📱 WhatsApp Business Cloud API (optional):")
    print("   Requires a Meta Business account and WhatsApp Business app.")
    print("   See plans/whatsapp_implementation_plan.md for setup guide.")
    wa_phone_id = input("WhatsApp Phone Number ID (leave blank to skip): ").strip()
    wa_enabled = False
    wa_biz_id = ""
    wa_access_token = ""
    wa_verify_token = ""
    wa_allow_from = []
    if wa_phone_id:
        wa_biz_id = input("WhatsApp Business Account ID: ").strip()
        wa_access_token = input("WhatsApp Access Token: ").strip()
        wa_verify_token = input("WhatsApp Verify Token (custom string for webhook): ").strip() or "myclaw_verify"
        wa_phone = input("Your WhatsApp phone number (e.g. 1234567890): ").strip()
        wa_allow_from = [wa_phone] if wa_phone else []
        wa_enabled = bool(wa_phone_id and wa_access_token)

    # ── Build config ──────────────────────────────────────────────────────────
    config = {
        "providers": {
            "ollama":     {"base_url": "http://localhost:11434"},
            "lmstudio":   {"base_url": "http://localhost:1234/v1",  "api_key": "lm-studio"},
            "llamacpp":   {"base_url": "http://localhost:8080/v1",  "api_key": "no-key"},
            "openai":     {"api_key": api_key if provider == "openai"     else ""},
            "anthropic":  {"api_key": api_key if provider == "anthropic"  else ""},
            "gemini":     {"api_key": api_key if provider == "gemini"     else ""},
            "groq":       {"api_key": api_key if provider == "groq"       else ""},
            "openrouter": {"api_key": api_key if provider == "openrouter" else ""},
        },
        "agents": {
            "defaults": {
                "model":    model,
                "provider": provider,
            }
        },
        "channels": {
            "telegram": {
                "enabled":   tg_enabled,
                "token":     tg_token or "YOUR_TOKEN",
                "allowFrom": [tg_user] if tg_user else ["YOUR_USER_ID"],
            },
            "whatsapp": {
                "enabled":             wa_enabled,
                "phone_number_id":     wa_phone_id or "YOUR_PHONE_NUMBER_ID",
                "business_account_id": wa_biz_id or "YOUR_BUSINESS_ACCOUNT_ID",
                "access_token":        wa_access_token or "YOUR_ACCESS_TOKEN",
                "verify_token":        wa_verify_token or "YOUR_VERIFY_TOKEN",
                "allowFrom":           wa_allow_from or ["YOUR_PHONE_NUMBER"],
            }
        },
    }

    # ── Knowledge Base ──────────────────────────────────────────────────────────
    print("\n📚 Knowledge Base:")
    print("  The knowledge base stores persistent notes in Markdown format.")
    print("  Files are stored in ~/.myclaw/knowledge/")
    
    kb_enabled = input("Enable knowledge base? [Y/n]: ").strip().lower()
    kb_enabled = kb_enabled in ("", "y", "yes")
    
    config["knowledge"] = {
        "enabled": kb_enabled,
        "auto_extract": False,
        "knowledge_dir": "~/.myclaw/knowledge"
    }
    
    save_config(config)
    print(f"\n✅ Config saved to {CONFIG_DIR}/config.json")
    
    # Create knowledge directory if enabled
    if kb_enabled:
        from pathlib import Path
        kb_dir = Path.home() / ".myclaw" / "knowledge" / "default"
        kb_dir.mkdir(parents=True, exist_ok=True)
        print(f"📁 Knowledge directory created: {kb_dir}")

    if provider in {"ollama", "lmstudio", "llamacpp"}:
        print(f"\nMake sure your {provider} server is running, then: python cli.py agent")
    else:
        print("\nRun `python cli.py agent` to start chatting.")
    
    print("\nKnowledge commands:")
    print("  /knowledge search <query>  - Search knowledge base")
    print("  /knowledge write <title>   - Create a new note")
    print("  /knowledge list            - List all notes")