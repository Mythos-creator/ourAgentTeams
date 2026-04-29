# ourAgentTeams

My blog : https://aclitice.com

在终端里**拉起一支本地 AI 小队**：由 Leader 规划任务，把子任务分发给不同模型（本机或云端 API），最后**合并成一份结果**。  
[English](README.md) | **中文**

---

## 这是什么

**ourAgentTeams** 面向**多步骤、可拆解的工作**：你用自然语言说一次目标；它负责**拆任务、选模型、跟进度、给最终稿**，你不必为每一步反复写提示词。

**Leader** 跑在你本机（通过 [Ollama](https://ollama.com)）。可以**全本地**；需要时再**接入云 API** 作为“强力工人”。调度逻辑在本地；只有在你配置 API 工人时，才会把内容发到外部（并可配合脱敏降低泄露风险）。

---

## 能做什么

- **写代码、写文档、改设计** — 从需求到多段产出，再整合成可交付物  
- **研究类问题** — 分步分析、对比、成文（可结合历史任务检索）  
- **日常问答** — **Single 模式**下每条消息会路由到较合适的本机模型；**Team 模式**适合你想先**商量计划、再让整队执行**的大任务  
- **控成本** — 本地免费模型 + 按需付费 API；预算在配置里可调  

更细的架构与开发说明见 **[DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md)**。

---

## 你会得到什么

- **交互式终端** — 默认进入会话（Single / Team 模式）；也支持**一句话任务**的 `start`  
- **隐私辅助** — 外发前可扫描常见敏感信息  
- **模型记忆与报告** — 子任务质量与成本，便于判断哪些云模型还值得用  
- **工具与 RAG** — 工作流里可接本地工具与**历史任务检索**（按环境开启）  

---

## 环境要求

| | |
|--|--|
| **Ollama** | 本机安装并运行；至少 `ollama pull` 与 `config/config.yaml` 里 `leader.model` 一致（或改配置） |
| **Python** | 3.11+ |

---

## 安装

**方式 A — 自动脚本（会尝试 venv / conda / uv 等）**

```bash
bash setup.sh
# 按提示激活环境，如: source .venv/bin/activate
```

**方式 B — 最小步骤**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .
```

**方式 C** — 见 `Makefile` 中的 conda / Docker 等目标。

拉取 Ollama 能跑的模型（与 `config/config.yaml` 中 leader 配置一致或修改配置）：

```bash
ollama pull qwen2.5:7b    # 示例，按自己显存/内存选
```

---

## 怎么用

安装 `ouragentteams` 命令后（在安装了 `pip install -e .` 的同一 Python 环境）：

### 第 1 步：启动交互会话（推荐）

```bash
ouragentteams
```

- **Single（默认）**：常规对话；Leader 会为每条输入路由到较合适的模型。  
- **Team**：输入 `/team <任务>`，先定计划，再多工人协作执行。  
- 会话内常用：`/help`、`/mode`、`/clear`、`/exit`。

等价命令：

```bash
ouragentteams chat
```

### 第 2 步：非交互执行（一次性任务）

```bash
ouragentteams start "用自然语言描述你的任务"
```

适合脚本、自动化、非 TTY 环境。

### 第 3 步：管理 Leader 模型

查看本机 Ollama 已拉取模型：

```bash
ouragentteams leader list
```

切换 Leader 并持久化到配置：

```bash
ouragentteams leader use qwen3.5:4b
```

`switch` 方式（可选持久化）：

```bash
ouragentteams leader switch --model qwen3.5:4b
ouragentteams leader switch --model qwen3.5:4b --persist
```

### 第 4 步：管理 Worker（增删查，改=删后重加）

查看 worker 列表：

```bash
ouragentteams config list-workers
```

添加本地 worker：

```bash
ouragentteams config add-worker --model gemma4:e2b --local
```

添加云端 API worker：

```bash
ouragentteams config add-worker --model gpt-4o --api-key <密钥> --strengths "coding,analysis"
```

删除 worker：

```bash
ouragentteams config remove-worker --model gpt-4o
```

> 当前没有单独 `update` 命令；需要修改时，先删再按新参数添加。

连通性自检（Leader + 全部 workers）：

```bash
ouragentteams config verify
```

### 第 5 步：常用运维命令

```bash
ouragentteams reload    # 重新加载 config.yaml
ouragentteams report    # 模型表现汇总
ouragentteams --help    # 查看全部命令
```

---

## 目录速览

| 路径 | 作用 |
|------|------|
| `config/config.yaml` | Leader、工人、预算、隐私等 |
| `data/` | 会话、历史、向量库等（运行中生成） |
| `DEVELOPER_GUIDE.md` | 开发者详述与扩展说明 |

---

## 测试

```bash
pip install pytest
pytest -q
```

---

## 许可证

[MIT](LICENSE) — 见仓库根目录 `LICENSE` 文件。
