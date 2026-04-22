# ZenSynora Personal — Recommended LLM Configuration

> **Variant:** Personal (local-first, solo user, privacy-focused)  
> **Date:** 2026-04-20  
> **Goal:** Pick the right local model for your hardware and use case.

---

## Executive Summary

For the **Personal variant**, the ideal LLM depends on your hardware. ZenSynora is agent-heavy (tool calling, multi-step reasoning, knowledge RAG, swarm coordination), so **tool-use reliability and context-window size matter more than raw chat quality**.

| Hardware Profile | Recommended Model | Size | VRAM Needed | Speed* | Best For |
|-----------------|-------------------|------|-------------|--------|----------|
| **High-end GPU** | **Qwen2.5-32B-Instruct** | 32B | ~22 GB (Q4) | ~25 t/s | Power user: complex swarms, large KB RAG |
| **Mid-range GPU** | **Qwen2.5-14B-Instruct** | 14B | ~10 GB (Q4) | ~45 t/s | **Sweet spot:** excellent tools, fast, affordable |
| **Entry GPU** | **Qwen2.5-7B-Instruct** | 7B | ~5 GB (Q4) | ~75 t/s | Budget builds, basic agent tasks |
| **CPU-only** | **Llama 3.2 3B** | 3B | ~2 GB (Q4) | ~8 t/s | Minimal hardware, offline-only mode |

\* *Tokens per second on typical consumer hardware. Measured with Ollama, Q4_K_M quantization.*

---

## Why Qwen2.5 for ZenSynora?

ZenSynora's Personal variant performs these LLM-intensive operations:

1. **Parallel tool calling** (`agent.py` lines 1200–1300) — The LLM must emit valid JSON tool calls, often multiple at once.
2. **Knowledge RAG** — Context windows include system prompt + memory history + KB search results (can exceed 8K tokens).
3. **Swarm coordination** — The orchestrator sends aggregated outputs back to the LLM for synthesis.
4. **Medic / NewTech analysis** — Long-form reasoning over logs, code diffs, and news articles.

**Qwen2.5** outperforms Llama 3.x and Mistral on **function-calling benchmarks** (BFCL, Nexus) while maintaining strong reasoning scores. Its 128K context window handles large RAG retrievals without truncation.

| Model | Tool-Use Score (BFCL) | MATH Reasoning | Context | ZenSynora Fit |
|-------|----------------------|----------------|---------|---------------|
| Qwen2.5-14B | **89.2** | 79.1 | 128K | ⭐ Best balance |
| Llama 3.3-70B | 84.5 | 83.2 | 128K | Great, needs more VRAM |
| Mistral-Small-3-24B | 82.1 | 81.5 | 128K | Good alternative |
| Qwen2.5-32B | **91.5** | 85.3 | 128K | Best quality, more VRAM |
| Llama 3.2-3B | 61.0 | 52.0 | 128K | Current default — limited |

---

## Tiered Hardware Recommendations

### Tier 1: High-End Workstation (RTX 4090 / 48GB+ VRAM)

**Model:** `qwen2.5:32b` (Q4_K_M)  
**VRAM:** ~22 GB  
**Speed:** ~25 tokens/s  
**Use Case:** Heavy swarm work, large knowledge bases (10K+ notes), coding assistance.

```bash
# Ollama setup
ollama pull qwen2.5:32b

# ZenSynora config (~/.myclaw/config.json)
{
  "agents": {
    "defaults": {
      "provider": "ollama",
      "model": "qwen2.5:32b"
    }
  }
}
```

**Why this tier:** The 32B parameter count handles complex multi-hop reasoning (e.g., "Analyze this error log, check the knowledge base for similar issues, propose a fix, and validate it with the Medic agent").

---

### Tier 2: Mid-Range Gaming GPU (RTX 3060 12GB / RTX 4060 Ti 16GB / RX 6800 XT)

**Model:** `qwen2.5:14b` (Q4_K_M) — **Recommended default for most users**  
**VRAM:** ~10 GB  
**Speed:** ~45 tokens/s  
**Use Case:** Daily driver — chat, knowledge search, tool execution, light swarm work.

```bash
ollama pull qwen2.5:14b
```

**Config:**
```json
{
  "agents": {
    "defaults": {
      "provider": "ollama",
      "model": "qwen2.5:14b"
    }
  }
}
```

**Performance expectation:**
- Simple chat response: **1–2 seconds**
- Tool call + execution + response: **3–5 seconds**
- Knowledge RAG with 20 retrieved notes: **4–6 seconds**
- 3-agent swarm synthesis: **8–12 seconds**

This is the **sweet spot** — 14B is the smallest model that reliably handles parallel tool calls and follows complex system prompts.

---

### Tier 3: Entry GPU (RTX 3050 6GB / GTX 1660 Ti 6GB / Integrated GPU)

**Model:** `qwen2.5:7b` (Q4_K_M)  
**VRAM:** ~5 GB  
**Speed:** ~75 tokens/s  
**Use Case:** Basic chat, single-tool calls, small knowledge base.

```bash
ollama pull qwen2.5:7b
```

**Trade-offs:**
- Tool calling is reliable for **single tools**, but parallel multi-tool execution may hallucinate or skip steps.
- Reasoning depth is adequate for simple Q&A but struggles with 3+ step planning.
- Recommend keeping `memory.summarization_threshold` low (e.g., 10 messages) to preserve context.

---

### Tier 4: CPU-Only / Low-Power Laptop (No discrete GPU)

**Model:** `llama3.2:3b` (current default)  
**VRAM/RAM:** ~2 GB  
**Speed:** ~8 tokens/s (Apple M3: ~25 t/s)  
**Use Case:** Offline fallback, quick queries, simple shell commands.

```bash
ollama pull llama3.2:3b
```

**Caveats:**
- Knowledge gap handling (`agent.py` lines 1000–1100) will trigger more often because the 3B model has weaker recall.
- Complex swarms should be limited to 2 agents max.
- Consider using cloud APIs (OpenAI/Groq) as a fallback for hard tasks by setting up dual providers.

---

## Alternative Models Worth Considering

| Model | When to Choose It |
|-------|-------------------|
| **Llama 3.3 70B** | If you have 48GB VRAM and want the best open-source reasoning. Slightly worse tool-use than Qwen2.5-32B but stronger on coding benchmarks. |
| **Mistral Small 3 (24B)** | If you want a European/Apache-2.0 model. Excellent context handling, very strong at 128K RAG tasks. |
| **DeepSeek-R1-Distill-Qwen-14B** | If your workflow is reasoning-heavy (math, code debugging, Medic agent log analysis). Slower due to chain-of-thought output, but higher accuracy. |
| **Phi-4 (14B)** | If you need a compact model with surprisingly strong reasoning. Good for laptops with 16GB unified memory (Apple Silicon). |
| **Command R (35B)** | If you primarily do knowledge-base RAG. Built for retrieval-augmented generation with excellent citation accuracy. |

---

## Context Window Budget for ZenSynora

ZenSynora's `agent.py` builds a context window from:

| Component | Typical Size | Notes |
|-----------|-------------|-------|
| System prompt | 500–1,500 tokens | Agent personality + available tools schema |
| Memory history | 2,000–6,000 tokens | Last N messages (configurable) |
| Knowledge RAG | 1,000–4,000 tokens | Top-K retrieved notes |
| Tool results | 500–3,000 tokens | Shell output, web browse content |
| User message | 50–500 tokens | Current query |
| **Total** | **~4,000–15,000 tokens** | **128K context is overkill but safe** |

**Recommendation:** Even the 7B model's 128K window is sufficient. The bottleneck is not context length — it's the model's ability to **pay attention to the right parts** of that context. Qwen2.5 models handle this well.

---

## Ollama Tuning for ZenSynora

Create a custom Modelfile to optimize for agentic behavior:

```dockerfile
FROM qwen2.5:14b

# Lower temperature for more deterministic tool calls
PARAMETER temperature 0.3

# Higher repeat penalty to avoid loops (important for Agent.think())
PARAMETER repeat_penalty 1.15

# Slightly higher top-p for creative synthesis tasks
PARAMETER top_p 0.9

# System prompt tuned for tool use
SYSTEM """You are ZenSynora, a helpful AI assistant with access to tools.
When a user asks a question, first determine if a tool is needed.
If multiple tools are independent, call them in parallel.
Always respond in the user's language. Be concise but thorough."""
```

```bash
ollama create zensynora-agent -f ./Modelfile
```

Then set `model: "zensynora-agent"` in config.

---

## Dual-Provider Fallback Strategy

For CPU-only users or when local models struggle, configure a cloud fallback:

```json
{
  "agents": {
    "defaults": {
      "provider": "ollama",
      "model": "qwen2.5:14b"
    }
  },
  "providers": {
    "groq": {
      "api_key": "YOUR_GROQ_KEY",
      "enabled": true
    }
  },
  "intelligence": {
    "routing": {
      "enabled": true,
      "allowed_models": ["qwen2.5:14b", "llama3-70b-8192"],
      "allowed_providers": ["ollama", "groq"]
    }
  }
}
```

ZenSynora's `IntelligentRouter` (`backends/router.py`) can automatically fall back to Groq's `llama3-70b-8192` when:
- Local Ollama is unreachable
- The query is flagged as complex (coding, math, multi-step)
- The user explicitly prefixes with `/cloud`

---

## Summary

| Your Setup | Recommended Model | One-Liner |
|-----------|-------------------|-----------|
| RTX 4090 / 48GB+ | `qwen2.5:32b` | Maximum capability for heavy agent work |
| RTX 3060 12GB / 4060 Ti | `qwen2.5:14b` | **Best value — the daily driver** |
| GTX 1660 / 3050 | `qwen2.5:7b` | Fast and capable for basic tasks |
| No GPU / laptop | `llama3.2:3b` | Minimal, but consider cloud fallback |
| Apple M3/M4 Pro | `qwen2.5:14b` or `phi4` | Excellent unified memory performance |

**Bottom line:** Start with `qwen2.5:14b`. It is the best-balanced open model for agentic workloads as of early 2026, and it runs comfortably on the most common consumer GPU (12–16 GB VRAM).

---

*Generated: 2026-04-20*
