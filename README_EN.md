# ourAgentTeams — Local-LLM-Driven Multi-Agent Work Queue

[中文](README.md) | **English**

ourAgentTeams is a multi-agent task orchestration system that uses a **local large language model as the Team Leader**. Simply submit a task from the command line — the Leader analyzes and decomposes it, assigns subtasks to the best available workers (local or cloud API) based on capability and cost, monitors execution in real time, reviews output quality, and delivers the final integrated result. No manual intervention required.

All orchestration logic runs locally. The Leader **never** sends sensitive information to external APIs.

---

## Key Features

| Feature | Description |
|---------|-------------|
| **Local Leader** | Runs local LLMs via Ollama (Qwen, Llama, DeepSeek, etc.) — no API key required, fully offline capable |
| **Automatic Task Decomposition** | The Leader breaks complex tasks into dependency-ordered subtasks and executes them concurrently |
| **Smart Model Routing** | Automatically selects the best worker based on capability profiles, task importance, and remaining budget |
| **Real-time Monitoring & Failover** | Heartbeat files detect stalled workers; automatic failover to a backup model with no data loss |
| **Cost Management** | Precise token counting via tiktoken; critical tasks use the best model, low-priority tasks use free local models |
| **Privacy Protection** | Scans for PII / credentials / secrets before dispatch; only sanitized text is sent to external models; Leader keeps the original locally |
| **Performance Memory** | Scores each worker after every task; automatically flags models that are no longer worth paying for |
| **User Preference Learning** | Stores user habits as a natural-language paragraph injected into the Leader's context — influences output style without manual config |
| **MCP Tool Integration** | Leader can invoke file read/write, directory listing, code search, and shell commands |
| **RAG Retrieval Augmentation** | Completed task results are vectorized; retrieved as context for future related tasks |

---

## Requirements

- **Ollama** (required): installed and running on the host machine for local model inference  
  → Download: [https://ollama.com](https://ollama.com)
- Python 3.11+ (managed automatically by the setup script — no manual install needed)

---

## Quick Start

> **Step 0 (all paths): Install Ollama and pull a Leader model**
>
> ```bash
> # Pull a Leader model (choose based on your VRAM)
> ollama pull qwen2.5:7b       # Entry-level, works with 4 GB VRAM
> ollama pull qwen2.5:14b      # Balanced option
> ollama pull qwen2.5:72b      # High-performance, strong multilingual ability
> ollama pull deepseek-r1:32b  # Best reasoning
> ```

---

### Path A — Auto-install (recommended for all users)

The script auto-detects uv / conda / venv and picks the best available method:

```bash
bash setup.sh
```

Follow the prompt at the end to activate the environment (`source .venv/bin/activate` or `conda activate ouragentteams`).

---

### Path B — conda (for users who already have conda)

```bash
make setup-conda
conda activate ouragentteams
```

conda automatically installs Python 3.11 — no manual Python version management needed.

---

### Path C — Docker (for users who prefer not to install Python)

```bash
make setup-docker
docker compose run --rm app --help
```

> Ollama still runs on the host machine; the container connects to it automatically via `host.docker.internal:11434`.  
> `config/` and `data/` directories are volume-mounted, so your settings persist across container rebuilds.

---

### Step 2 — (Optional) Add cloud API workers

```bash
# Add workers interactively
python -m src.cli.main config add-worker \
  --model claude-3-5-sonnet-20241022 \
  --api-key "sk-ant-xxx" \
  --strengths "backend,code_review,analysis"

python -m src.cli.main config add-worker \
  --model gemini-1.5-pro \
  --api-key "AIzaSy-xxx" \
  --strengths "frontend,multimodal,long_context"

python -m src.cli.main config add-worker \
  --model gpt-4o \
  --api-key "sk-xxx" \
  --strengths "writing,reasoning,general"
```

Or edit `config/config.yaml` directly (supports environment variable references):

```yaml
workers:
  api:
    - model: claude-3-5-sonnet-20241022
      api_key: ${ANTHROPIC_API_KEY}
      strengths: [backend, code_review, analysis]
```

### Step 3 — Submit a task

```bash
python -m src.cli.main start "Build a REST API with JWT authentication using FastAPI and PostgreSQL"
```

Example execution output:

```
╔══════════════════════════════════════════════════╗
║   ourAgentTeams — sess_20260416_143021_a3b9c2    ║
╠══════════════════════════════════════════════════╣
║ Leader: qwen2.5:72b (local)                      ║
║ State: received                                  ║
╚══════════════════════════════════════════════════╝

[Privacy] Scanning for sensitive information...
No sensitive information detected

[Analysis] Leader is analyzing the task...
[Planning] Decomposing into subtasks...

Analysis: A complete backend API project requiring database design,
          authentication logic, route implementation, and documentation.

Decomposed into 4 subtasks

Task Plan
├── ... [1] Database model design  ->  claude-3-5-sonnet-20241022  (importance: 8)
├── ... [2] JWT authentication     ->  claude-3-5-sonnet-20241022  (importance: 9)
├── ... [3] API route implementation -> gpt-4o                     (importance: 7)
└── ... [4] API documentation      ->  qwen2.5:7b (local)          (importance: 4)

[Executing] Running subtasks...
  >>> sub_1 -> claude-3-5-sonnet-20241022
  OK  sub_1 (claude-3-5-sonnet-20241022) — 1842 tokens, $0.0028, 14.3s
  >>> sub_2 -> claude-3-5-sonnet-20241022
  OK  sub_2 (claude-3-5-sonnet-20241022) — 2103 tokens, $0.0031, 16.7s
  >>> sub_3 -> gpt-4o
  OK  sub_3 (gpt-4o) — 1677 tokens, $0.0042, 11.2s
  >>> sub_4 -> qwen2.5:7b
  OK  sub_4 (qwen2.5:7b) — 1203 tokens, $0.0000, 28.1s

[Reviewing] Leader is reviewing results...
  [Review] sub_1: score 9.0 PASS
  [Review] sub_2: score 8.5 PASS
  [Review] sub_3: score 7.5 PASS
  [Review] sub_4: score 8.0 PASS

[Integrating] Merging final output...

[Delivered] Task complete!
┌─ Cost ───────────────────────────────────────────┐
│  ██████████░░░░░░░░░░  $0.0101 / $20.00          │
│  remaining: $19.9899                             │
└──────────────────────────────────────────────────┘

═══ Final Output ═══

# FastAPI + PostgreSQL + JWT Authentication REST API
... (full code output)
```

---

## Full CLI Reference

### Task Submission

```bash
# Submit a task (basic)
python -m src.cli.main start "your task description"

# Set a per-task budget override (USD)
python -m src.cli.main start "task description" --budget 0.5

# Hot-reload config.yaml without restarting
python -m src.cli.main reload
```

### Leader Model Management

```bash
# List all locally available Ollama models
python -m src.cli.main leader list

# Switch the Leader model (persisted to config.yaml)
python -m src.cli.main leader use qwen2.5:72b
python -m src.cli.main leader use deepseek-r1:32b

# Temporarily switch the Leader (session only, does not modify config.yaml)
python -m src.cli.main leader switch --model llama3.3:70b

# Switch and persist to config.yaml
python -m src.cli.main leader switch --model llama3.3:70b --persist
```

### Worker Model Management

```bash
# List all configured worker models
python -m src.cli.main config list-workers

# Add a local Ollama worker
python -m src.cli.main config add-worker --model qwen2.5:14b --local

# Add a cloud API worker
python -m src.cli.main config add-worker \
  --model gemini-1.5-pro \
  --api-key "AIzaSy-xxx" \
  --strengths "frontend,multimodal"

# Remove a worker
python -m src.cli.main config remove-worker --model gpt-4-turbo

# Verify connectivity for all models (runs a ping test on each)
python -m src.cli.main config verify
```

### User Preference Management

Preferences are stored as a natural-language paragraph that is injected into the Leader's system context at the start of each session. This directly affects task granularity, output format, and model selection — no structured config required.

```bash
# View your current preference summary
python -m src.cli.main profile show

# View raw JSON (advanced users)
python -m src.cli.main profile show --raw

# Enter conversation mode — tell the Leader your preferences in plain language
python -m src.cli.main profile edit
# > I don't want you to ask for confirmation at every step — just execute
# > Write all code comments in English
# > I prefer TypeScript + FastAPI stack
# > done

# Reset all preferences to defaults
python -m src.cli.main profile reset
```

Preferences are also **passively learned**: after each task, the Leader analyzes your rework reasons, positive/negative feedback signals, and the direction of any edits you make to deliverables — and updates your profile automatically.

### Performance Reports & Cost Advice

```bash
# View the performance summary table for all models
python -m src.cli.main report

# View detailed stats for a specific model
python -m src.cli.main report --model gpt-4o

# Get cost optimization suggestions (which models aren't worth keeping)
python -m src.cli.main report --suggest-savings
```

Example report output:

```
              Model Performance Report
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Model                       ┃ Avg Score ┃ Tasks ┃ Fail Rate ┃ Review Pass  ┃ Cost    ┃ Verdict               ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━┩
│ claude-3-5-sonnet-20241022  │    8.9    │  44   │    2%     │     91%      │ $1.320  │ recommended           │
│ gemini-1.5-pro              │    8.7    │  47   │    6%     │     89%      │ $0.850  │ recommended           │
│ gpt-4o                      │    6.1    │  23   │    0%     │     70%      │ $3.870  │ consider_replacing    │
│ gpt-4-turbo                 │    4.3    │  12   │   17%     │     58%      │ $2.100  │ not_worth_paying      │
│ qwen2.5:7b (local)          │    7.2    │  31   │    3%     │     84%      │ $0.000  │ usable                │
└─────────────────────────────┴───────────┴───────┴───────────┴──────────────┴─────────┴───────────────────────┘

Savings Suggestions
  Stop paying for gpt-4-turbo: quality (4.3) no better than local (7.2), spent $2.10  (save ~$2.10/mo)
  Demote gpt-4o to backup: declining quality trend, use only as fallback            (save ~$2.32/mo)

  Total potential savings: $4.42/mo
```

---

## Configuration Reference (config/config.yaml)

```yaml
# ── Leader ───────────────────────────────────────────────────
leader:
  model: qwen2.5:72b          # Primary Leader model
  provider: ollama
  ollama_base_url: http://localhost:11434
  fallbacks:                  # Auto-failover order if Leader crashes
    - model: deepseek-r1:32b
      provider: ollama
    - model: llama3.3:70b
      provider: ollama
    - model: qwen2.5:14b
      provider: ollama
  heartbeat_interval_s: 5     # How often the Leader writes its heartbeat
  watchdog_timeout_s: 30      # Time before Watchdog declares Leader dead

# ── Worker pool ───────────────────────────────────────────────
workers:
  local:                      # Free local Ollama models
    - model: qwen2.5:7b
      provider: ollama
    - model: llama3.2:3b
      provider: ollama
  api:                        # Paid cloud models (api_key supports env vars)
    - model: claude-3-5-sonnet-20241022
      api_key: ${ANTHROPIC_API_KEY}
      strengths: [backend, code_review, analysis]
    - model: gemini-1.5-pro
      api_key: ${GOOGLE_API_KEY}
      strengths: [frontend, multimodal, long_context]
    - model: gpt-4o
      api_key: ${OPENAI_API_KEY}
      strengths: [writing, reasoning, general]

# ── Monitor ───────────────────────────────────────────────────
monitor:
  heartbeat_interval_s: 10    # How often workers write heartbeat files
  timeout_threshold_s: 120    # No heartbeat beyond this = timeout
  max_retries: 3              # Max failover attempts per subtask

# ── Cost control ─────────────────────────────────────────────
cost:
  monthly_budget_usd: 20.0    # Monthly budget cap; exceed it → auto-downgrade to local
  importance_threshold_local: 5   # Subtasks scoring < 5 go to local models
  importance_threshold_best: 8    # Subtasks scoring ≥ 8 get the best model

# ── Privacy ───────────────────────────────────────────────────
privacy:
  enabled: true
  entities:                   # Entity types to detect and redact
    - PERSON
    - EMAIL_ADDRESS
    - PHONE_NUMBER
    - CREDIT_CARD
    - API_KEY
    - PASSWORD
```

API key formats:
- Inline value: `api_key: "sk-ant-xxx..."` — suitable for local private environments
- Environment variable: `api_key: ${ANTHROPIC_API_KEY}` — suitable for teams and CI

---

## Running Tests

```bash
python -m pytest tests/ -v
```

Test coverage: config loading and env-var injection, privacy detection and sanitize/restore, token counting and cost routing, model performance profile updates, heartbeat monitoring and timeout detection, task decomposition JSON parsing.

---

## FAQ

**Q: Leader fails to start with an Ollama connection error.**
Confirm the Ollama service is running (`ollama serve`) and that `leader.ollama_base_url` in `config.yaml` matches the actual address (default: `http://localhost:11434`).

**Q: How do I switch to a smaller local model to save VRAM?**
```bash
python -m src.cli.main leader use qwen2.5:14b
```

**Q: Can I run entirely on local models with no cloud API at all?**
Yes. Keep only `workers.local` entries in `config.yaml` and remove `workers.api`. All subtasks will be handled by local Ollama models at zero cost.

**Q: Privacy scan didn't catch my custom sensitive term. How do I extend it?**
Edit `config/privacy_rules.yaml` and add a custom regex under `custom_patterns`:
```yaml
custom_patterns:
  - name: INTERNAL_PROJECT_ID
    regex: 'PROJ-[A-Z0-9]{8}'
    score: 0.9
```

**Q: How can I review the detailed execution history of a past task?**
Task history is stored in `data/task_history.db` (SQLite) and can be opened with any SQLite client. You can also use `report --model <name>` to review a specific model's historical performance.

**Q: A worker model got stuck. What happens?**
The Monitor detects the missing heartbeat after `monitor.timeout_threshold_s` (default 120 seconds), reassigns the subtask to a fallback model, and retries automatically — up to `max_retries` (default 3) times. No manual intervention needed.
