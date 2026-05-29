# Offline Evaluation Harness

独立离线评测模块。不依赖 `src/leader/orchestrator.py`，直接驱动
`BaseModelWorker.chat()` 完成"候选模型生成 → 成对评审 → ELO 排名"。

## 目录

```
eval/
├── benchmarks/          # YAML 基准集（coding / reasoning / creative）
├── judges/pairwise.md   # 成对评审 system prompt
├── elo.py               # 标准 ELO 表
├── runner.py            # 主流程 + CLI
├── report.py            # JSON → Markdown 渲染
└── results/             # 运行后生成的 JSON / Markdown
```

## 用法

### 1. 比较两个本地 Ollama 模型在 coding 基准上的表现

```bash
python -m eval.runner \
    --bench coding \
    --candidates qwen2.5-coder:14b llama3.1:8b \
    --judge qwen2.5:14b
```

### 2. 用 Agent 角色（agents/*.md）当作候选

```bash
python -m eval.runner --bench reasoning --agents --candidates generalist coder
```

Agent 模式下，runner 会从 `agents/<name>.md` 读取
`preferred_models` / `fallback_models` 解析出实际模型，并把 frontmatter 后
的正文当作 system prompt。

### 3. 渲染 Markdown 报告

```bash
python -m eval.report eval/results/coding.json
# → eval/results/coding.md
```

## 自定义基准

新增 `eval/benchmarks/<name>.yaml`：

```yaml
name: my_bench
description: 自定义任务集
required_skills: [coding]
items:
  - id: my_001
    title: 题目标题
    prompt: |
      题面正文
    reference: 参考答案要点（可选）
```

无需注册，直接 `--bench my_bench` 即可运行。

## 备注

- Judge 默认使用 `cfg.leader.model`；可通过 `--judge` 覆盖。
- 默认开启 A/B 位置交换（`--no-swap` 关闭）以缓解 position bias。
- ELO 默认 K=32，初始 1000。
- 失败的生成不会阻断流程：错误信息保存在 `error` 字段、对应回答为空，由 judge
  自然给对手让分。
