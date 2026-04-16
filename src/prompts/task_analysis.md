# 任务分析模板

你正在分析一个新提交的任务。请按以下步骤思考：

## 第一步：理解目标
- 用户想要达成什么？
- 有哪些明确的要求和隐含的期望？
- 交付物是什么形式（代码、文档、设计、分析报告）？

## 第二步：评估复杂度
- 这个任务涉及几个技术领域？
- 是否有子任务之间的依赖关系？
- 预估总工作量（token 消耗）

## 第三步：识别所需能力
对每个子任务，标注需要的核心能力：
- frontend / backend / database / devops
- writing / analysis / reasoning / creative
- code_review / testing / documentation
- multimodal / search / long_context

## 第四步：规划执行顺序
- 哪些子任务可以并行？
- 哪些必须串行（有数据依赖）？
- 关键路径是什么？

## 第五步：输出结构化计划
严格按 JSON 格式输出，包含 analysis 和 subtasks 数组。
