# ourAgentTeams — 本地大模型驱动的多智能体工作队列

**中文** | [English](README_EN.md)

ourAgentTeams 是一个以**本地大模型为 Team Leader** 的多智能体任务编排系统。用户只需通过命令行提交任务，Leader 负责分析拆解、按能力和成本分配给合适的工作模型（本地或云端 API）、实时监控执行进度、Review 输出质量，最终整合交付——全程无需人工干预。

所有调度逻辑均在本地运行，Leader 永远不会把敏感信息发送给外部 API。

---

## 核心特性

| 特性 | 说明 |
|------|------|
| **本地 Leader** | 使用 Ollama 运行本地大模型（Qwen、Llama、DeepSeek 等），无需 API Key，完全离线可用 |
| **任务自动拆解** | Leader 将复杂任务分解为带依赖关系的子任务，并发执行 |
| **智能模型路由** | 根据各模型能力档案 + 任务重要性 + 剩余预算，自动选择最合适的执行者 |
| **实时监控失效转移** | 心跳文件机制检测卡死，超时自动切换备用模型继续执行，无数据丢失 |
| **成本管理** | tiktoken 精确计量 token，重要任务用最优模型，低优先级任务走免费本地模型 |
| **隐私保护** | 任务提交前自动扫描 PII / 密钥 / 凭证，脱敏后才发给外部模型，Leader 本地保留原文 |
| **绩效记忆** | 每次任务后对执行模型打分，自动识别"不值得续费的模型"并给出成本建议 |
| **用户偏好学习** | 以自然语言段落记录用户习惯，注入 Leader 上下文，无需手动配置即可影响输出风格 |
| **MCP 工具集成** | Leader 可调用文件读写、目录浏览、代码搜索、Shell 命令等工具 |
| **RAG 检索增强** | 历史任务结果向量化存储，新任务执行时自动检索相关上下文 |

---

## 环境要求

- **Ollama**（必须）：在宿主机安装并运行，用于本地大模型推理  
  → 下载地址：[https://ollama.com](https://ollama.com)
- Python 3.11+（由安装脚本自动管理，无需手动安装）

---

## 快速开始

> **第一步（所有路径通用）：安装并启动 Ollama，拉取 Leader 模型**
>
> ```bash
> # 拉取 Leader 模型（任选一个，取决于你的显存）
> ollama pull qwen2.5:7b       # 入门推荐，4 GB 显存可用
> ollama pull qwen2.5:14b      # 均衡选项
> ollama pull qwen2.5:72b      # 高性能，中文能力强
> ollama pull deepseek-r1:32b  # 推理能力强
> ```

---

### 路径 A — 自动安装（推荐，适合所有用户）

脚本自动检测 uv / conda / venv，选择最优方式安装：

```bash
bash setup.sh
```

完成后按提示激活环境即可（`source .venv/bin/activate` 或 `conda activate ouragentteams`）。

---

### 路径 B — conda（适合已有 conda 的用户）

```bash
make setup-conda
conda activate ouragentteams
```

conda 会自动安装 Python 3.11，无需手动管理 Python 版本。

---

### 路径 C — Docker（适合不想安装 Python 环境的用户）

```bash
make setup-docker
docker compose run --rm app --help
```

> Ollama 依然运行在宿主机，Docker 容器通过 `host.docker.internal:11434` 自动连接。  
> `config/` 和 `data/` 目录挂载为 volume，容器重建不丢失配置。

---

### 第二步：（可选）添加云端 API Worker

```bash
# 交互式添加
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

也可以直接编辑 `config/config.yaml`（支持环境变量引用）：

```yaml
workers:
  api:
    - model: claude-3-5-sonnet-20241022
      api_key: ${ANTHROPIC_API_KEY}
      strengths: [backend, code_review, analysis]
```

### 第三步：提交任务

```bash
python -m src.cli.main start "帮我构建一个带 JWT 认证的 REST API，使用 FastAPI 和 PostgreSQL"
```

执行过程示例：

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

Analysis: 这是一个完整的后端 API 项目，需要数据库设计、认证逻辑、路由实现和文档四个部分。

Decomposed into 4 subtasks

Task Plan
├── ... [1] 数据库模型设计  ->  claude-3-5-sonnet-20241022  (importance: 8)
├── ... [2] JWT 认证逻辑    ->  claude-3-5-sonnet-20241022  (importance: 9)
├── ... [3] API 路由实现    ->  gpt-4o                      (importance: 7)
└── ... [4] 接口文档编写    ->  qwen2.5:7b (本地)           (importance: 4)

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

# FastAPI + PostgreSQL + JWT 认证 REST API
...（完整代码输出）
```

---

## 完整 CLI 命令参考

### 任务提交

```bash
# 提交任务（基础用法）
python -m src.cli.main start "你的任务描述"

# 为本次任务单独设置预算上限（USD）
python -m src.cli.main start "任务描述" --budget 0.5

# 热重载配置文件（不重启进程）
python -m src.cli.main reload
```

### Leader 模型管理

```bash
# 列出本机 Ollama 中已拉取的所有可用模型
python -m src.cli.main leader list

# 切换 Leader 模型（持久化写入 config.yaml）
python -m src.cli.main leader use qwen2.5:72b
python -m src.cli.main leader use deepseek-r1:32b

# 临时切换 Leader（不修改 config.yaml，仅本次会话生效）
python -m src.cli.main leader switch --model llama3.3:70b

# 切换并持久化
python -m src.cli.main leader switch --model llama3.3:70b --persist
```

### Worker 模型管理

```bash
# 查看所有已配置的 Worker 模型
python -m src.cli.main config list-workers

# 添加本地 Worker（Ollama 模型）
python -m src.cli.main config add-worker --model qwen2.5:14b --local

# 添加云端 API Worker
python -m src.cli.main config add-worker \
  --model gemini-1.5-pro \
  --api-key "AIzaSy-xxx" \
  --strengths "frontend,multimodal"

# 移除 Worker
python -m src.cli.main config remove-worker --model gpt-4-turbo

# 验证所有模型连通性（含 ping 测试）
python -m src.cli.main config verify
```

### 用户偏好管理

偏好以自然语言段落存储，每次会话开始时自动注入 Leader 的系统上下文，直接影响任务拆解粒度、输出格式、模型选择等行为。

```bash
# 查看当前偏好摘要
python -m src.cli.main profile show

# 查看原始 JSON（高级用户）
python -m src.cli.main profile show --raw

# 进入对话模式，直接用自然语言告诉 Leader 你的偏好
python -m src.cli.main profile edit
# > 我不喜欢你在执行过程中每步都问我确认，直接做就好
# > 代码注释统一用中文写
# > 我偏好 TypeScript + FastAPI 技术栈
# > done

# 清空偏好重置
python -m src.cli.main profile reset
```

偏好也会被 Leader **被动学习**：每次任务结束后，Leader 会分析你的返工原因、正负面反馈词、对交付物的修改方向，自动更新偏好档案，无需你主动操作。

### 绩效报告与成本建议

```bash
# 查看所有模型的绩效汇总表
python -m src.cli.main report

# 查看单个模型的详细数据
python -m src.cli.main report --model gpt-4o

# 获取成本优化建议（Leader 分析哪些模型不值得续费）
python -m src.cli.main report --suggest-savings
```

报告示例：

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
  停用 gpt-4-turbo: 质量(4.3)不优于本地模型(7.2)，已花费$2.10  (save ~$2.10/mo)
  降级为备用 gpt-4o: 近期质量下滑，建议仅作备选              (save ~$2.32/mo)

  Total potential savings: $4.42/mo
```

---

## 配置文件详解（config/config.yaml）

```yaml
# ── Leader ───────────────────────────────────────────────────
leader:
  model: qwen2.5:72b          # 主 Leader 模型
  provider: ollama
  ollama_base_url: http://localhost:11434
  fallbacks:                  # Leader 崩溃时的自动转移顺序
    - model: deepseek-r1:32b
      provider: ollama
    - model: llama3.3:70b
      provider: ollama
    - model: qwen2.5:14b
      provider: ollama
  heartbeat_interval_s: 5     # Leader 写心跳频率
  watchdog_timeout_s: 30      # Watchdog 判定 Leader 超时的阈值

# ── Worker 模型池 ─────────────────────────────────────────────
workers:
  local:                      # 本地 Ollama 模型，零费用
    - model: qwen2.5:7b
      provider: ollama
    - model: llama3.2:3b
      provider: ollama
  api:                        # 云端付费模型（api_key 支持环境变量）
    - model: claude-3-5-sonnet-20241022
      api_key: ${ANTHROPIC_API_KEY}
      strengths: [backend, code_review, analysis]
    - model: gemini-1.5-pro
      api_key: ${GOOGLE_API_KEY}
      strengths: [frontend, multimodal, long_context]
    - model: gpt-4o
      api_key: ${OPENAI_API_KEY}
      strengths: [writing, reasoning, general]

# ── 监控参数 ──────────────────────────────────────────────────
monitor:
  heartbeat_interval_s: 10    # Worker 写心跳的频率
  timeout_threshold_s: 120    # 超过此时间无心跳则判定超时
  max_retries: 3              # 最多重试/转移次数

# ── 成本控制 ──────────────────────────────────────────────────
cost:
  monthly_budget_usd: 20.0    # 月度预算上限，超限自动降级为本地模型
  importance_threshold_local: 5   # 重要性 < 5 的子任务走本地模型
  importance_threshold_best: 8    # 重要性 ≥ 8 的子任务用最优模型

# ── 隐私保护 ──────────────────────────────────────────────────
privacy:
  enabled: true
  entities:                   # 需要检测并脱敏的实体类型
    - PERSON
    - EMAIL_ADDRESS
    - PHONE_NUMBER
    - CREDIT_CARD
    - API_KEY
    - PASSWORD
```

API Key 的两种写法：
- 直接写值：`api_key: "sk-ant-xxx..."` — 适合本地私有环境
- 环境变量引用：`api_key: ${ANTHROPIC_API_KEY}` — 适合团队共享或 CI 环境

---

## 系统工作原理

### 任务完整生命周期

```
用户提交任务
    │
    ▼
[隐私扫描] ── Presidio + 正则规则检测 PII/密钥
    │         敏感内容由 Leader 本地保留，对外仅发脱敏版本
    ▼
[任务分析] ── Leader LLM 理解目标、约束、交付物形式
    │
    ▼
[任务拆解] ── 生成带依赖关系的子任务列表，每个子任务有重要性评分 (1-10)
    │
    ▼
[模型分配] ── 按能力档案 + 成本层级 (local/mid/best) 为每个子任务选模型
    │
    ▼
[并发执行] ── 无依赖的子任务并发启动，有依赖的按顺序等待
    │         每个 Worker 每 10s 写心跳文件
    │
    ├── [Monitor] 持续轮询心跳 → 超时 → 切换备用模型重新执行
    │
    ▼
[Review]   ── Leader 对每个子任务输出打分，不通过则触发返工
    │
    ▼
[整合交付] ── Leader 将所有子任务结果合并为完整交付物
    │         还原脱敏占位符，恢复原始上下文
    ▼
[记忆更新] ── 更新模型绩效档案、任务历史 (SQLite)、RAG 向量库
              被动推断用户偏好变化
```

### Leader 失效转移（Watchdog）

系统启动时会同时运行一个轻量 Watchdog 进程，独立监控 Leader 的心跳文件。当 Leader 崩溃（Ollama OOM、模型加载失败等），Watchdog 在 30 秒内检测到并自动：
1. 读取最近一次会话快照（`data/sessions/{id}/state.json`）
2. 按 `config.yaml` 中的 `leader.fallbacks` 列表依次尝试拉起备用 Leader
3. 新 Leader 加载快照，跳过已完成的子任务，从断点继续执行

### 成本路由决策

```
子任务重要性评分 (Leader 打分 1-10)
    < 5  →  local tier  → 优先使用本地 Ollama 模型（零费用）
    5-7  →  mid tier    → 选性价比最优的中档付费模型
    ≥ 8  →  best tier   → 选历史质量评分最高的付费模型

特例：月度预算已用尽 → 无论重要性，全部降级到 local tier
```

### 隐私保护流程

```
原始用户输入
    │
    ▼
Presidio 扫描 + 自定义正则
    │
    ├── 无敏感内容 → 直接发给 Worker
    │
    └── 有敏感内容 → 生成脱敏版本（替换为 [ENTITY_TYPE_xxxxx] 占位符）
                     Leader 本地保留：原始内容 + 占位符映射表
                     外部 Worker 只收到：脱敏版本
                     Worker 返回结果后：Leader 用映射表还原占位符
```

---

## 项目结构

```
ourAgentTeams/
├── config/
│   ├── config.yaml           # 主配置：Leader、Worker、监控、成本、隐私
│   ├── models_profile.json   # 模型能力档案（运行时自动更新）
│   └── privacy_rules.yaml    # 自定义敏感信息检测规则
│
├── src/
│   ├── config.py             # 配置加载、环境变量注入、热重载
│   ├── cli/
│   │   ├── main.py           # 所有 CLI 命令入口
│   │   └── display.py        # Rich 终端 UI 组件
│   ├── leader/
│   │   ├── orchestrator.py   # 核心状态机，串联全部模块
│   │   ├── task_planner.py   # 任务分析与子任务拆解
│   │   ├── model_selector.py # 能力匹配 + 成本路由
│   │   ├── monitor.py        # 心跳检测 + 超时失效转移
│   │   └── integrator.py     # Review + 结果整合
│   ├── models/
│   │   ├── base.py           # 抽象 Worker 接口
│   │   ├── local_model.py    # Ollama 本地模型封装
│   │   └── api_model.py      # LiteLLM 云端统一封装
│   ├── memory/
│   │   ├── capability_store.py  # 模型绩效档案（读写 models_profile.json）
│   │   ├── task_history.py      # SQLite 任务历史（SQLAlchemy ORM）
│   │   └── rag_engine.py        # ChromaDB 向量检索
│   ├── cost/
│   │   └── calculator.py     # Token 计量、费用估算、路由决策
│   ├── privacy/
│   │   └── guard.py          # Presidio + 正则，脱敏/还原
│   ├── mcp/
│   │   └── server.py         # MCP 工具注册（文件、搜索、Shell）
│   └── prompts/
│       ├── leader_system.md  # Leader 系统提示词（人格、职责、约束）
│       ├── task_analysis.md  # 任务分析提示模板
│       └── review.md         # Review 评审提示模板
│
├── data/                     # 运行时数据（自动生成）
│   ├── tasks/                # 子任务心跳文件
│   ├── sessions/             # 会话快照 + Leader 心跳
│   ├── memory/               # 用户偏好档案 (user_profile.json)
│   └── vectorstore/          # ChromaDB 持久化目录
│
├── tests/                    # 单元测试（28 项）
├── requirements.txt
├── pyproject.toml
└── README.md
```

---

## 运行测试

```bash
python -m pytest tests/ -v
```

测试覆盖：配置加载与环境变量注入、隐私检测与脱敏/还原、Token 计量与成本路由、模型绩效档案更新、心跳监控与超时检测、任务拆解 JSON 解析。

---

## 常见问题

**Q: Leader 启动失败，提示 Ollama 连接错误**
确认 Ollama 后台服务正在运行（`ollama serve`），且 `config.yaml` 中的 `leader.ollama_base_url` 与实际地址一致（默认 `http://localhost:11434`）。

**Q: 如何让 Leader 切换到更小的本地模型以节省显存？**
```bash
python -m src.cli.main leader use qwen2.5:14b
```

**Q: 我不想用任何云端 API，只用本地模型可以吗？**
完全可以。在 `config.yaml` 中只保留 `workers.local` 条目，删除 `workers.api` 即可。所有子任务将由本地 Ollama 模型执行，零费用。

**Q: 隐私扫描没有检测到我的自定义敏感词，如何扩展？**
编辑 `config/privacy_rules.yaml`，在 `custom_patterns` 下添加自定义正则：
```yaml
custom_patterns:
  - name: INTERNAL_PROJECT_ID
    regex: 'PROJ-[A-Z0-9]{8}'
    score: 0.9
```

**Q: 如何查看某次任务的详细执行记录？**
任务历史存储在 `data/task_history.db`（SQLite），可用任意 SQLite 客户端查看，或通过 `report --model <name>` 查看特定模型的历史表现。

**Q: Worker 模型执行卡死怎么办？**
Monitor 会在 `monitor.timeout_threshold_s`（默认 120 秒）后自动检测到心跳超时，并将该子任务转移给备用模型重新执行，最多重试 `max_retries`（默认 3）次，无需手动干预。
