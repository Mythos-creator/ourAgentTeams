# ourAgentTeams

My blog : https://aclitice.com

**Run a local AI “team” from your terminal** — a leader model plans work, other models (local or cloud) execute subtasks, and you get one merged result.  
[中文](README_CN.md) | **English**

---

## What it is

**ourAgentTeams** is a command-line assistant for **multi-step work**: you describe what you want once; the app breaks it into pieces, routes each piece to a suitable model, tracks progress, and returns a **single final answer** — without you managing prompts for every sub-step.

The **leader** runs on your machine (via [Ollama](https://ollama.com)). You can stay **fully local**, or **add cloud API models** when you need stronger or specialized workers. Orchestration stays on your side; sensitive text is not sent to the cloud until you choose API workers (and even then optional redaction helps).

---

## What you can use it for

- **Software & writing** — specs, refactors, docs, API design, boilerplate in chunks the system merges for you  
- **Research-style tasks** — breakdowns, comparisons, structured reports (with optional retrieval from past runs)  
- **Day-to-day questions** — interactive **Single** mode routes each message to a reasonable local model; **Team** mode is for bigger jobs you want planned and executed step-by-step  
- **Cost-aware workflows** — mix **free local** models with **paid APIs** only where it matters; budget caps in config

---

## What you get

- **Interactive CLI** — default session: chat with routing, or type `/team …` for full team planning + execution  
- **One-shot tasks** — `ouragentteams start "…"` for non-interactive runs (scripts, CI, automation)  
- **Privacy helpers** — scan for common secrets before sending text to external providers  
- **Model memory & reports** — learn which models pay off; optional savings hints  
- **Optional tools** — file / search / shell helpers in the workflow; **RAG** over past task text when enabled

Details, architecture, and contribution notes: **[DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md)**.

---

## Requirements


|            |                                                                                                                            |
| ---------- | -------------------------------------------------------------------------------------------------------------------------- |
| **Ollama** | Install and run on the same machine you use the CLI; pull at least the model set in `config/config.yaml` as `leader.model` |
| **Python** | 3.11+                                                                                                                      |


---

## Install

**Option A — script (picks venv / conda / uv when present)**

```bash
bash setup.sh
# then: source .venv/bin/activate   # or follow the script’s hint
```

**Option B — minimal**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .
```

**Option C — see** `Makefile` **for** `conda` / **Docker** targets.

Pull a model Ollama can run (match or update `leader.model` in `config/config.yaml`):

```bash
ollama pull qwen2.5:7b    # example; pick one that fits your GPU/RAM
```

---

## How to use

After install, the `ouragentteams` command is on your `PATH` (same environment where you ran `pip install -e .`).

### Step 1: Start interactive session

```bash
ouragentteams
```

- **Single mode (default)** — normal chat; leader routes each turn to a suitable model.
- **Team mode** — type `/team <task>` for plan + multi-worker execution.
- In-session helpers: `/help`, `/mode`, `/clear`, `/exit`.

Equivalent command:

```bash
ouragentteams chat
```

### Step 2: Run one-shot tasks (non-interactive)

```bash
ouragentteams start "Your task in natural language"
```

Useful for scripts/automation or non-TTY environments.

### Step 3: Manage Leader model

List local models (from Ollama):

```bash
ouragentteams leader list
```

Set Leader model and persist to config:

```bash
ouragentteams leader use qwen3.5:4b
```

Quick switch via option-style command:

```bash
ouragentteams leader switch --model qwen3.5:4b
ouragentteams leader switch --model qwen3.5:4b --persist
```

### Step 4: Manage workers (CRUD-style)

List workers:

```bash
ouragentteams config list-workers
```

Add a local worker:

```bash
ouragentteams config add-worker --model gemma4:e2b --local
```

Add an API worker:

```bash
ouragentteams config add-worker --model gpt-4o --api-key <key> --strengths "coding,analysis"
```

Remove a worker:

```bash
ouragentteams config remove-worker --model gpt-4o
```

> Update worker config: remove then add again with new fields (there is no dedicated `update` command yet).

Verify connectivity for leader + all workers:

```bash
ouragentteams config verify
```

### Step 5: Useful ops

```bash
ouragentteams reload    # reload config.yaml
ouragentteams report    # model performance summary
ouragentteams --help    # all commands
```

---

## Project layout (quick map)


| Path                 | Purpose                                              |
| -------------------- | ---------------------------------------------------- |
| `config/config.yaml` | Leader model, workers, budget, privacy               |
| `data/`              | Sessions, history, vector store (created at runtime) |
| `DEVELOPER_GUIDE.md` | Deep docs for developers                             |


---

## Tests

```bash
pip install pytest
pytest -q
```

---

## License

[MIT](LICENSE) — see the `LICENSE` file in the repository root.