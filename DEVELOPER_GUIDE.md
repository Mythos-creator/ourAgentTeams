# ourAgentTeams — 开发者指南

本文档面向希望深入使用、定制或二次开发 ourAgentTeams 的开发者，涵盖：

- 项目代码结构导读
- 如何修改和扩展 Leader 系统提示词
- 如何添加 MCP 工具（Skills）
- 如何扩展 RAG 知识库
- 如何接入新的模型提供商
- 各模块的扩展接口说明
- 可迭代开发的功能方向

---

## 一、整体代码结构导读

在动手修改前，先理解各模块之间的调用关系：

```
用户 CLI 输入
      │
      ▼
src/cli/main.py           ← 命令解析，用户唯一交互入口
      │
      ▼
src/leader/orchestrator.py ← 核心状态机，串联所有模块
      │
      ├── src/privacy/guard.py         隐私扫描，生成脱敏版本
      ├── src/leader/task_planner.py   调用 Leader LLM 拆解任务
      ├── src/memory/rag_engine.py     检索历史上下文
      ├── src/leader/model_selector.py 按能力+成本分配模型
      ├── src/models/local_model.py    Ollama Worker 执行
      ├── src/models/api_model.py      云端 API Worker 执行
      ├── src/leader/monitor.py        心跳监控 + 失效转移
      ├── src/leader/integrator.py     Review + 结果整合
      └── src/memory/capability_store.py 更新模型绩效档案
```

**修改任何单一功能，只需找到对应模块，其他模块不受影响。**

---

## 二、修改 Leader 系统提示词

### 文件位置

```
src/prompts/leader_system.md   ← Leader 人格、职责、输出格式规范
src/prompts/task_analysis.md   ← 任务分析阶段的思考框架
src/prompts/review.md          ← Review 评分标准和维度
```

### leader_system.md 的结构

```markdown
# 静态部分（每次会话都注入）
你是 ourAgentTeams 的 Team Leader...
## 核心身份
## 职责边界
## 沟通风格
## 输出格式规范    ← 必须保留 JSON 格式约定，否则解析会失败
## 风险意识

---

# 动态注入区域（由 Orchestrator 在运行时填充）
## 用户偏好
{user_preferences}           ← 自动替换为 user_profile.json 中的自然语言摘要

## 当前可用工作模型
{worker_capabilities}        ← 自动替换为各模型的能力档案摘要

## 当前预算状态
{budget_status}              ← 自动替换为剩余预算信息
```

> **注意**：`{user_preferences}`、`{worker_capabilities}`、`{budget_status}` 是占位符，不能删除，否则 Orchestrator 注入时会报错。输出格式中的 JSON 结构（`analysis` + `subtasks` 数组）也必须保留，因为 `task_planner.py` 依赖这个格式进行解析。

### 常见定制场景

**场景 1：让 Leader 用英文工作**

在 `## 沟通风格` 部分修改：
```markdown
## 沟通风格
- 简洁、专业、直接
- 始终用英文与用户沟通
- ...
```

**场景 2：限制子任务数量**

在 `## 职责边界` → `你要做的` 中添加：
```markdown
3. **任务拆解**：将复杂任务分解为 2-6 个子任务，不要拆得过细
```

**场景 3：添加特定领域知识**

在静态部分末尾添加新章节：
```markdown
## 领域知识
本团队主要服务于金融科技项目，请在任务分析时注意：
- 所有涉及金额计算的代码必须使用 Decimal 而非 float
- 接口设计需符合 RESTful 规范
- 数据库操作必须在子任务中包含事务处理要求
```

**场景 4：调整 Review 标准**

编辑 `src/prompts/review.md`，修改各维度权重或通过分数阈值：
```markdown
## 评分标准
passed = true 当 quality_score >= 7   ← 默认是 6，提高标准
```

---

## 三、添加 MCP 工具（Skills）

MCP 工具让 Leader 能够与本地环境交互——读写文件、执行命令、调用外部 API 等。

### 文件位置

```
src/mcp/server.py   ← MCPToolRegistry 类，所有工具在此注册
```

### 内置工具一览

| 工具名 | 功能 |
|--------|------|
| `read_file` | 读取文件内容（最多 50000 字符）|
| `write_file` | 写入内容到文件 |
| `list_directory` | 列出目录下的文件和子目录 |
| `search_files` | 在目录中全文搜索关键词 |
| `run_command` | 执行 shell 命令（限时 30 秒）|

### 添加新工具的步骤

打开 `src/mcp/server.py`，在 `_register_builtins` 方法末尾（或新建方法中）调用 `self.register()`：

```python
def _register_builtins(self) -> None:
    # ... 已有工具 ...

    # 示例：添加一个 HTTP 请求工具
    self.register(ToolDefinition(
        name="http_get",
        description="发送 HTTP GET 请求并返回响应体",
        parameters={
            "url":     {"type": "string", "description": "请求 URL"},
            "timeout": {"type": "integer", "description": "超时秒数", "default": 10},
        },
        handler=self._http_get,
    ))

def _http_get(self, url: str, timeout: int = 10) -> str:
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.read().decode("utf-8")[:10_000]
    except Exception as e:
        return f"Error: {e}"
```

### 更多工具示例

**Git 操作工具：**

```python
self.register(ToolDefinition(
    name="git_status",
    description="获取当前 git 仓库的状态",
    parameters={},
    handler=lambda: self._run_command("git status --short"),
))

self.register(ToolDefinition(
    name="git_diff",
    description="获取指定文件的 git diff",
    parameters={"path": {"type": "string", "description": "文件路径"}},
    handler=lambda path: self._run_command(f"git diff -- {path}"),
))
```

**数据库查询工具：**

```python
def _register_db_tools(self, db_url: str) -> None:
    import sqlite3

    def query_db(sql: str) -> str:
        try:
            conn = sqlite3.connect(db_url)
            cursor = conn.execute(sql)
            rows = cursor.fetchmany(50)
            return "\n".join(str(r) for r in rows)
        except Exception as e:
            return f"Error: {e}"

    self.register(ToolDefinition(
        name="query_database",
        description="执行 SQL 查询并返回结果（最多50行）",
        parameters={"sql": {"type": "string", "description": "SQL 查询语句"}},
        handler=query_db,
    ))
```

**网络搜索工具（接入搜索 API）：**

```python
def _web_search(self, query: str, num_results: int = 5) -> str:
    # 接入 SerpAPI / Tavily / DuckDuckGo API
    import os, json, urllib.request, urllib.parse
    api_key = os.environ.get("SERPAPI_KEY", "")
    params = urllib.parse.urlencode({"q": query, "api_key": api_key, "num": num_results})
    url = f"https://serpapi.com/search.json?{params}"
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            data = json.loads(r.read())
        results = data.get("organic_results", [])[:num_results]
        return "\n".join(f"- {r['title']}: {r['snippet']}" for r in results)
    except Exception as e:
        return f"Search error: {e}"
```

### 让 Leader 知道工具可用

在 `src/prompts/leader_system.md` 的适当位置添加工具说明，或在 `Orchestrator` 中把 `mcp_registry.get_tools_description()` 的返回值注入到 Leader 的提示词中：

```python
# src/leader/orchestrator.py 中，在构建 plan_task 的 prompt 前
from src.mcp.server import MCPToolRegistry
mcp = MCPToolRegistry(workspace_root=str(Path.cwd()))
tools_desc = mcp.get_tools_description()
# 将 tools_desc 加入发给 Leader 的 prompt
```

---

## 四、扩展 RAG 知识库

RAG 模块使用 ChromaDB 做向量存储，支持将任何文本索引进去，在任务执行时自动检索相关上下文。

### 文件位置

```
src/memory/rag_engine.py   ← 核心函数：add_document / query / index_task_result
data/vectorstore/          ← ChromaDB 持久化目录（自动生成）
```

### 当前的自动索引内容

每次任务完成后，`Orchestrator._record_to_memory()` 会自动调用：

```python
# src/memory/rag_engine.py
index_task_result(task_id, description, result_summary, model)
# 存储格式："Task: xxx\nModel: yyy\nResult: zzz"
```

### 手动向 RAG 注入自定义知识

在项目中任何地方调用 `add_document`：

```python
from src.memory.rag_engine import add_document

# 注入项目文档
add_document(
    doc_id="project_architecture",
    text="本项目采用 FastAPI + PostgreSQL 架构，所有 API 路由在 app/routers/ 目录下...",
    metadata={"type": "project_doc", "source": "architecture.md"},
)

# 注入代码规范
add_document(
    doc_id="coding_standards",
    text="所有函数必须有类型注解。错误处理统一使用自定义 AppError 异常类...",
    metadata={"type": "standards"},
)

# 注入历史决策记录
add_document(
    doc_id="decision_2026_04_jwt",
    text="2026-04-16: 决定使用 RS256 而非 HS256 算法签发 JWT，原因是需要支持多服务验证...",
    metadata={"type": "decision", "date": "2026-04-16"},
)
```

### 批量导入文档（初始化知识库脚本示例）

新建 `scripts/init_rag.py`：

```python
"""批量初始化 RAG 知识库"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.memory.rag_engine import add_document

docs_dir = Path("docs")  # 你的文档目录

for md_file in docs_dir.rglob("*.md"):
    content = md_file.read_text(encoding="utf-8")
    doc_id = md_file.stem
    print(f"Indexing {md_file}...")
    add_document(
        doc_id=doc_id,
        text=content[:8000],  # ChromaDB 单文档限制
        metadata={"source": str(md_file), "type": "documentation"},
    )

print("Done.")
```

运行：`python scripts/init_rag.py`

### 更换向量模型（使用更强的 Embedding）

默认 ChromaDB 使用内置的 all-MiniLM-L6-v2，如需更换：

```python
# src/memory/rag_engine.py 中修改 _ensure_collection()

from chromadb.utils import embedding_functions

# 使用 OpenAI embedding
ef = embedding_functions.OpenAIEmbeddingFunction(
    api_key=os.environ.get("OPENAI_API_KEY"),
    model_name="text-embedding-3-small",
)

# 使用本地 SentenceTransformer
ef = embedding_functions.SentenceTransformerEmbeddingFunction(
    model_name="BAAI/bge-large-zh-v1.5"  # 中文效果更好
)

_collection = _client.get_or_create_collection(
    name="agent_team_memory",
    embedding_function=ef,
    metadata={"hnsw:space": "cosine"},
)
```

---

## 五、接入新的模型提供商

### 方法一：通过 LiteLLM（推荐，零代码）

LiteLLM 支持 100+ 模型提供商。只需在 `config.yaml` 中使用对应的模型名格式：

```yaml
workers:
  api:
    # Mistral AI
    - model: mistral/mistral-large-latest
      api_key: ${MISTRAL_API_KEY}
      strengths: [reasoning, code]

    # Cohere
    - model: cohere/command-r-plus
      api_key: ${COHERE_API_KEY}
      strengths: [writing, analysis]

    # DeepSeek API（云端版）
    - model: deepseek/deepseek-chat
      api_key: ${DEEPSEEK_API_KEY}
      strengths: [reasoning, code, Chinese]

    # Azure OpenAI
    - model: azure/my-gpt4o-deployment
      api_key: ${AZURE_OPENAI_API_KEY}
      strengths: [general]

    # 自托管 / 兼容 OpenAI 接口的模型（如 vLLM、LM Studio）
    - model: openai/my-local-model
      api_key: none
      strengths: [code]
      # 在环境变量中设置: OPENAI_API_BASE=http://localhost:8000/v1
```

### 方法二：实现自定义 Worker 类

继承 `BaseModelWorker`，实现 `chat`、`ping`、`list_models` 三个方法：

```python
# src/models/custom_model.py
from src.models.base import BaseModelWorker, ModelResponse
import time

class MyCustomWorker(BaseModelWorker):
    """接入任意自定义推理端点"""

    def __init__(self, model: str, *, endpoint: str, api_key: str = "", **kwargs):
        super().__init__(model, **kwargs)
        self._endpoint = endpoint
        self._api_key = api_key

    async def chat(self, messages, *, temperature=0.7, max_tokens=4096) -> ModelResponse:
        import aiohttp
        t0 = time.perf_counter()
        payload = {"model": self.model, "messages": messages,
                   "temperature": temperature, "max_tokens": max_tokens}
        headers = {"Authorization": f"Bearer {self._api_key}"}

        async with aiohttp.ClientSession() as session:
            async with session.post(self._endpoint, json=payload, headers=headers) as resp:
                data = await resp.json()

        content = data["choices"][0]["message"]["content"]
        elapsed = time.perf_counter() - t0
        return ModelResponse(
            content=content,
            model=self.model,
            prompt_tokens=data.get("usage", {}).get("prompt_tokens", 0),
            completion_tokens=data.get("usage", {}).get("completion_tokens", 0),
            elapsed_s=round(elapsed, 2),
        )

    async def ping(self) -> bool:
        try:
            resp = await self.chat([{"role": "user", "content": "hi"}], max_tokens=5)
            return bool(resp.content)
        except Exception:
            return False

    async def list_models(self) -> list[str]:
        return [self.model]
```

然后在 `src/leader/orchestrator.py` 的 `_create_worker` 函数中注册：

```python
def _create_worker(model: str, cfg: AppConfig) -> BaseModelWorker:
    # 新增：匹配自定义 Worker
    if model.startswith("custom/"):
        from src.models.custom_model import MyCustomWorker
        return MyCustomWorker(
            model=model,
            endpoint="http://my-inference-server/v1/chat/completions",
            api_key=os.environ.get("MY_API_KEY", ""),
        )
    # 已有逻辑...
    for w in cfg.workers_api:
        if w.model == model:
            return APIModelWorker(model=model, api_key=w.api_key)
    return OllamaWorker(model=model, base_url=cfg.leader.ollama_base_url)
```

### 更新定价表

新增模型后，在 `src/cost/calculator.py` 的 `MODEL_PRICING` 字典中添加价格（单位：每百万 token 的 USD）：

```python
MODEL_PRICING: dict[str, tuple[float, float]] = {
    # ... 已有模型 ...
    "mistral/mistral-large-latest": (2.00, 6.00),   # (输入, 输出)
    "deepseek/deepseek-chat":       (0.14, 0.28),
    "cohere/command-r-plus":        (3.00, 15.00),
}
```

---

## 六、各模块扩展接口速查

### 6.1 添加新的隐私检测规则

编辑 `config/privacy_rules.yaml`：

```yaml
custom_patterns:
  - name: INTERNAL_ID          # 自定义实体名
    regex: 'EMP-\d{6}'         # 正则表达式
    score: 0.95                # 置信度 0-1

  - name: CHINESE_ID_CARD
    regex: '\d{17}[\dXx]'
    score: 0.98

  - name: WECHAT_ID
    regex: '[a-zA-Z][a-zA-Z0-9_-]{5,19}'
    score: 0.7
```

无需修改任何 Python 代码，重启后自动生效。

### 6.2 修改任务拆解的 Prompt

编辑 `src/leader/task_planner.py` 中的 `PLAN_PROMPT_TEMPLATE`：

```python
PLAN_PROMPT_TEMPLATE = """\
你是一个多智能体团队的 Leader...

## 额外约束
- 所有子任务必须包含单元测试要求
- 代码类子任务必须标注 estimated_tokens >= 3000
- ...
"""
```

### 6.3 自定义模型选择逻辑

在 `src/leader/model_selector.py` 中修改 `select_model_for_subtask` 函数。例如，添加"特定关键词强制指定模型"逻辑：

```python
def select_model_for_subtask(subtask, cfg, cost_tracker):
    # 新增：关键词强制路由
    force_map = {
        "security": "claude-3-5-sonnet-20241022",
        "database": "gpt-4o",
    }
    for kw, forced_model in force_map.items():
        if kw in subtask.description.lower():
            for w in cfg.workers_api:
                if w.model == forced_model:
                    return w

    # 原有逻辑继续...
    tier = pick_tier(subtask.importance, ...)
```

### 6.4 在 Orchestrator 中插入自定义钩子

`Orchestrator` 支持通过 `set_progress_callback` 注入任意异步回调函数，在每个事件触发时执行自定义逻辑：

```python
# 示例：任务完成后发送钉钉/Slack 通知
async def my_callback(event: str, data: dict) -> None:
    if event == "delivered":
        await send_dingtalk_message(
            f"任务完成！花费 ${data['cost']['spent_usd']:.4f}"
        )
    elif event == "failover":
        await send_dingtalk_message(
            f"⚠️ 子任务 {data['subtask_id']} 发生失效转移 -> {data['new_model']}"
        )

orch = Orchestrator(cfg)
orch.set_progress_callback(my_callback)
result = await orch.run("你的任务")
```

支持的事件类型：

| 事件名 | 触发时机 | data 字段 |
|--------|----------|-----------|
| `state` | 状态机每次切换 | `state`, `cost`(delivered时) |
| `privacy` | 隐私扫描完成 | `has_sensitive`, `entity_count` |
| `plan` | 任务拆解完成 | `analysis`, `subtask_count` |
| `assignment` | 模型分配完成 | `assignments[]` |
| `subtask_start` | 子任务开始执行 | `subtask_id`, `model`, `retry` |
| `subtask_done` | 子任务执行完成 | `subtask_id`, `tokens`, `cost`, `elapsed_s` |
| `subtask_error` | 子任务执行出错 | `subtask_id`, `error`, `retry` |
| `failover` | 触发失效转移 | `subtask_id`, `new_model` |
| `review` | 单个子任务 Review 完成 | `subtask_id`, `score`, `passed` |
| `rework` | 触发返工 | `subtask_id`, `reason` |
| `leader_switch` | Leader 热切换 | `new_model`, `persisted` |
| `error` | 任务失败 | `error` |

---

## 七、可迭代开发的功能方向

以下是基于当前架构自然延伸的开发方向，按难度从低到高排列：

### 初级迭代（1-3天）

#### 1. 添加 Watchdog 进程（Leader 故障自动恢复）

当前 Leader 心跳写入逻辑已在 `src/leader/monitor.py` 的 `write_leader_heartbeat` 和 `check_leader_alive` 中实现，但 Watchdog 进程本身还未作为独立进程启动。

新建 `src/watchdog.py`：

```python
"""独立 Watchdog 进程，监控 Leader 存活并在崩溃时自动切换"""
import time, subprocess, sys
from src.leader.monitor import check_leader_alive
from src.leader.orchestrator import SessionSnapshot
from src.config import load_config

def main():
    cfg = load_config()
    while True:
        time.sleep(cfg.leader.watchdog_timeout_s // 2)
        if not check_leader_alive(cfg.leader.watchdog_timeout_s):
            print("[Watchdog] Leader timeout detected, attempting failover...")
            for fb in cfg.leader.fallbacks:
                cfg.leader.model = fb["model"]
                # 重启 Leader 进程，加载最新快照
                subprocess.Popen([sys.executable, "-m", "src.cli.main",
                                   "start", "--resume-session", "latest"])
                break

if __name__ == "__main__":
    main()
```

在 `ouragentteams start` 命令中同时启动 Watchdog：`subprocess.Popen([sys.executable, "-m", "src.watchdog"])`

#### 2. 对话式任务澄清

在任务开始前，让 Leader 先判断任务是否描述清晰，若不清晰则提问：

在 `src/leader/task_planner.py` 中新增 `clarify_task` 函数，在 `orchestrator.py` 的 `ANALYSIS` 状态中插入调用。

#### 3. 本地 Web UI 看板

用 Flask 或 FastAPI 暴露一个本地 HTTP 接口，用 WebSocket 推送 `_emit` 事件到浏览器，展示实时任务进度看板。

#### 4. 任务模板库

新建 `config/task_templates.yaml`，预置常用任务的拆解模板，跳过 Leader 的分析阶段直接执行：

```yaml
templates:
  - name: api_project
    description: "完整 REST API 项目"
    subtasks:
      - title: "数据库模型"
        importance: 8
        required_skills: [backend, database]
      - title: "业务逻辑层"
        importance: 8
        required_skills: [backend]
      - title: "路由与控制器"
        importance: 7
        required_skills: [backend]
      - title: "测试用例"
        importance: 6
        required_skills: [testing]
```

### 中级迭代（1-2周）

#### 5. 多用户支持

当前系统只有一个 `user_profile.json`。扩展为多用户：
- `data/users/{user_id}/profile.json`
- CLI 新增 `--user` 参数或从环境变量读取
- 每个用户独立的偏好、任务历史、RAG 命名空间

#### 6. 子任务间上下文传递

当前子任务相互独立。增加**上下文链**：子任务 B 执行时可以读取依赖的子任务 A 的输出结果，并注入到自己的 prompt 中。

修改 `src/leader/orchestrator.py` 的 `_execute_single`，在构建 `WORKER_PROMPT` 时附加前驱任务的结果摘要：

```python
context_parts = []
for dep_id in subtask.depends_on:
    dep = next((s for s in self.plan.subtasks if s.id == dep_id), None)
    if dep and dep.result:
        context_parts.append(f"前置任务《{dep.title}》的结果：\n{dep.result[:500]}")

prompt = WORKER_PROMPT.format(
    description=subtask.description,
    context="\n\n".join(context_parts) or "无前置依赖"
)
```

#### 7. 流式输出（Streaming）

当前 Worker 等待模型完整返回后才继续。改为流式：

```python
# src/models/local_model.py 中添加 stream_chat 方法
async def stream_chat(self, messages, **kwargs):
    client = self._ensure_client()
    async for chunk in await client.chat(
        model=self.model,
        messages=messages,
        stream=True,
    ):
        yield chunk["message"]["content"]
```

CLI 侧用 `Rich.Live` 实时渲染输出内容。

#### 8. 定时任务调度

新增 `ouragentteams schedule` 命令，支持 cron 表达式触发任务：

```bash
ouragentteams schedule "每天8点生成日报" --cron "0 8 * * *" \
  --task "根据昨天的 git log 生成开发日报并保存到 reports/ 目录"
```

使用 APScheduler 或 crontab 实现。

#### 9. 任务依赖图可视化

导出子任务依赖关系为 DOT/Mermaid 格式，在 CLI 中渲染：

```bash
ouragentteams start "复杂任务" --dry-run --show-graph
```

输出类似：
```
sub_1 (DB设计) ──→ sub_2 (逻辑层) ──→ sub_4 (集成测试)
               ──→ sub_3 (路由层) ──┘
```

### 高级迭代（2-4周）

#### 10. 多 Agent 协作（Agent-to-Agent）

当前 Worker 是无状态的单次调用。升级为有状态的 Agent：Worker 可以调用 MCP 工具、进行多轮对话、主动向 Leader 请求更多信息。

架构变化：Worker 从"被动执行函数"变为"独立协程"，通过消息队列与 Leader 通信。

#### 11. 长任务持久化恢复

支持任务执行跨越多次进程重启（断网、关机等）。当前已有会话快照机制，需要新增：
- 进程启动时检测未完成的 session
- `ouragentteams resume <session_id>` 命令
- `ouragentteams sessions list` 查看历史会话列表

```bash
ouragentteams sessions list
# sess_20260416_143021  [executing] 2/4 subtasks  $0.012
# sess_20260415_091230  [delivered] 4/4 subtasks  $0.031

ouragentteams resume sess_20260416_143021
```

#### 12. Leader 自我进化（Few-shot 记忆）

将高质量的任务拆解案例存入 RAG，Leader 在分析新任务时检索这些案例作为 few-shot 示例，逐步提升拆解质量。

新增 `src/memory/few_shot_store.py`，在 Review 通过且质量分 ≥ 9 时自动存入。

#### 13. 插件系统

定义 `src/plugins/base.py` 的插件基类，允许外部开发者编写插件来扩展工具、模型、输出格式等，通过配置文件动态加载：

```yaml
# config/config.yaml
plugins:
  - path: ./my_plugins/jira_integration.py
  - path: ./my_plugins/slack_notify.py
```

#### 14. 多模态任务支持

当前只处理文本。扩展支持：
- 图片输入（截图 + 图表分析）
- 音频转录（Whisper 本地运行）
- 代码仓库整体分析（批量注入 RAG）

在 `src/models/base.py` 的 `BaseModelWorker.chat` 接口中增加 `attachments` 参数，在支持视觉的模型（GPT-4o、Gemini、Claude 3.5）中实现。

---

## 八、开发调试技巧

### 本地测试单个模块

```python
# 直接测试 RAG 检索
from src.memory.rag_engine import add_document, query
add_document("test1", "FastAPI 是一个现代 Python Web 框架")
results = query("Python web 框架推荐")
print(results)

# 直接测试隐私扫描
from src.privacy.guard import PrivacyGuard
guard = PrivacyGuard()
result = guard.sanitize("我的邮箱是 test@example.com，API Key 是 sk-abc123")
print(result.sanitized)
print(result.placeholder_map)

# 直接测试成本估算
from src.cost.calculator import estimate_cost, count_tokens
tokens = count_tokens("这是一段测试文本" * 100)
cost = estimate_cost(tokens, tokens, "claude-3-5-sonnet-20241022")
print(f"{tokens} tokens → ${cost:.4f}")
```

### 查看会话快照内容

```bash
# 列出所有会话
ls data/sessions/

# 查看某次会话的状态
cat data/sessions/sess_20260416_143021_a3b9c2/state.json | python3 -m json.tool
```

### 查看模型绩效档案

```bash
cat config/models_profile.json | python3 -m json.tool
```

### 运行测试

```bash
# 运行全部测试
python -m pytest tests/ -v

# 只运行特定模块的测试
python -m pytest tests/test_privacy.py -v
python -m pytest tests/test_cost.py -v

# 带覆盖率报告
pip install pytest-cov
python -m pytest tests/ --cov=src --cov-report=term-missing
```

---

## 九、目录与文件速查表

| 想做的事 | 修改的文件 |
|----------|-----------|
| 修改 Leader 人格和行为规范 | `src/prompts/leader_system.md` |
| 修改任务拆解的思考框架 | `src/prompts/task_analysis.md` |
| 调整 Review 评分标准 | `src/prompts/review.md` |
| 添加 MCP 工具 | `src/mcp/server.py` |
| 扩展隐私检测规则 | `config/privacy_rules.yaml` |
| 接入新模型（LiteLLM） | `config/config.yaml` + `src/cost/calculator.py` |
| 实现自定义 Worker | `src/models/` 新建文件 + `src/leader/orchestrator.py` |
| 修改模型选择逻辑 | `src/leader/model_selector.py` |
| 向 RAG 注入知识 | 调用 `src/memory/rag_engine.add_document()` |
| 替换 RAG 的向量模型 | `src/memory/rag_engine.py → _ensure_collection()` |
| 修改任务拆解 Prompt | `src/leader/task_planner.py → PLAN_PROMPT_TEMPLATE` |
| 修改 Worker 执行 Prompt | `src/leader/orchestrator.py → WORKER_PROMPT` |
| 修改 Review Prompt | `src/leader/integrator.py → REVIEW_PROMPT` |
| 添加新的 CLI 命令 | `src/cli/main.py` |
| 调整心跳/超时参数 | `config/config.yaml → monitor` |
| 添加新模型定价 | `src/cost/calculator.py → MODEL_PRICING` |
| 监听任务执行事件 | `Orchestrator.set_progress_callback()` |
| 修改用户偏好存储结构 | `src/config.py → load_user_profile()` |
