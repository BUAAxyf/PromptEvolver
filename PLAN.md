# Codex Prompt Optimizer CLI + Skill 职责划分方案

## Summary

- 目标：设计一个给 Codex 使用的 prompt 优化工具链，输入 Mustache prompt 模板和 JSON 变量文件，迭代得到更适合目标模型完成任务的优化后 prompt。
- 核心分工：
  - **CLI 负责目标模型执行、候选 prompt 管理、优化搜索、产物记录**。
  - **Codex 负责理解任务、做 Judge、解释失败原因、调用 CLI、整理报告**。
- 首版不做“CLI 调 Codex”。Codex 是外部编排者，CLI 是可重复调用的本地优化引擎。

## 职责边界

| 能力 | CLI | Codex Skill |
|---|---|---|
| 读取 prompt 模板 | 是 | 是，负责检查语义和变量含义 |
| 读取变量文件 | 是 | 是，负责理解任务意图 |
| Mustache 渲染 | 是 | 可查看渲染结果 |
| 调用目标模型执行任务 | 是 | 否 |
| 生成候选 prompt | 是 | 可给出优化建议，但不直接作为主优化器 |
| 候选 prompt 版本管理 | 是 | 读取并解释 |
| Judge 打分 | 否，首版不内置 | 是 |
| 失败原因分析 | 记录 Codex 输入 | 是 |
| 优化停止判断 | 是，基于 Codex 写回的分数 | 是，辅助解释是否足够 |
| 最终报告 | 生成机器报告 | 生成用户可读报告 |

## CLI 设计

- CLI 名称：`codex-prompt-opt`。
- CLI 是目标模型优化器，不负责语义评审。
- 核心命令：
  - `validate`：校验 prompt 模板、Mustache 变量、JSON 变量文件。
  - `render`：把 prompt + case variables 渲染为完整任务实例。
  - `run`：用目标模型执行所有 cases，生成模型输出。
  - `judge-pack`：把 case、rendered prompt、target output、rubric 打包成 Codex 可评审文件。
  - `ingest-judgement`：读取 Codex 写回的 judgement JSON。
  - `propose`：基于历史 judgement 和失败分析生成下一版 prompt。
  - `optimize-step`：执行一轮 `run -> judge-pack`，等待 Codex judge。
  - `finalize`：选择最佳 prompt，输出 `best_prompt.md` 和报告。
- CLI 内部能力：
  - 使用 DSPy 的 `dspy.LM` 调用目标模型。
  - 管理候选 prompt：`candidate_id`、父候选、分数、变更摘要、失败样例。
  - 基于 Codex judgement 做 GEPA-lite 反思式改写。
  - 只信任结构化 judgement，不自行判断任务是否完成。

## Codex Skill 设计

- Skill 名称：`codex-prompt-optimizer`。
- Skill 是 CLI 的智能控制层和 Judge 层。
- Codex 工作流：
  1. 读取用户给定 prompt 和变量 JSON。
  2. 调用 `codex-prompt-opt validate`。
  3. 调用 `codex-prompt-opt optimize-step` 生成目标模型输出和 judge pack。
  4. Codex 根据变量文件中的任务说明、expected、rubric，对每个 case 打分。
  5. Codex 写回 `judgement.json`。
  6. 调用 `codex-prompt-opt ingest-judgement`。
  7. 若未达阈值，调用 `codex-prompt-opt propose` 或下一轮 `optimize-step`。
  8. 达标或预算耗尽后调用 `finalize`。
- Codex judgement 输出必须结构化：
  - `binary_score`: 0 或 1。
  - `score_100`: 0-100。
  - `rationale`: 为什么这样评分。
  - `failure_tags`: 如 `format_error`、`missing_field`、`wrong_label`、`hallucination`。
  - `improvement_advice`: 给下一版 prompt 的可执行修改建议。
- Codex 不能直接改变量文件；如发现变量文件/rubric 不足，只能报告问题或建议用户补充。

## 数据与产物

- 输入：
  - `prompt.md`：Mustache prompt 模板。
  - `task.json`：单文件多样例变量文件。
- 中间产物：
  - `rendered_cases.jsonl`：每个 case 的完整渲染 prompt。
  - `target_outputs.jsonl`：目标模型输出。
  - `judge_pack.json`：给 Codex judge 的材料。
  - `judgement.json`：Codex 写回的评分与诊断。
  - `candidates.jsonl`：候选 prompt 历史。
- 输出：
  - `best_prompt.md`：优化后 prompt。
  - `run_report.md`：用户可读优化报告。
  - `summary.json`：机器可读结果，包括最佳分数、通过率、迭代次数、模型配置。

## Implementation Notes

- 已落地首版代码实现，CLI 入口为 `codex-prompt-opt`。
- 首版 CLI 不实现 LLM Judge，也不调用 Codex。
- Judge 由 Codex Skill 完成，CLI 通过文件协议接收 judgement 结果。
- CLI 使用 DSPy 调用目标模型，并基于 judgement 做 GEPA-lite 反思式 prompt template 改写。
- 变量文件首版使用 JSON，且一个文件包含多个 cases。
- 评分同时保留 0/1 和 0-100 两种形式。
- 优化对象只限 prompt template；CLI 不自动修改变量文件。
