# ourAgentTeams

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

### 交互（推荐）

```bash
ouragentteams
```

- **Single（默认）** — 直接输入；Leader 为每条话选较合适的本机模型。  
- **Team** — 如输入 `/team 用 FastAPI 写带健康检查的小服务`：先**多轮改计划**，再**全队执行**子任务。  
- 会话内可用 `/help`、`/mode`、`/clear`、`/exit` 等。

与 `ouragentteams chat` 相同。

### 非交互、一句话任务

```bash
ouragentteams start "用自然语言描述你的任务"
```

适合脚本、自动化、无终端界面时。

### 云端 API（可选）

为子任务增加云侧工人，例如：

```bash
ouragentteams config add-worker --model <模型名> --api-key <密钥> --strengths "coding,analysis"
```

或在 `.env` 配环境变量、在 `config/config.yaml` 里引用。各厂商示例见 **DEVELOPER_GUIDE.md**。

### 其它常用命令

```bash
ouragentteams --help          # 全部子命令
ouragentteams leader list     # 本机 Ollama 已拉取模型
ouragentteams report          # 模型表现汇总
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
