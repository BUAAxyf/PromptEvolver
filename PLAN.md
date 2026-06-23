# Codex Prompt Optimizer CLI + Skill 重新设计方案

## Summary

- 目标：设计一个给 Codex 使用的 prompt 优化工具链，输入 Mustache prompt 模板和 JSON 变量文件，迭代得到更适合目标模型完成任务的优化后 prompt。
- 重新设计后的核心分工：
  - **CLI 负责可重复的目标模型执行与评测产物生成**：校验、渲染、DSPy 调用、judge pack、judgement 计分、最终产物写出。
  - **Codex master agent 负责流程编排与 prompt 改写**：收集 subagent 评审，综合优化策略，直接生成下一版 prompt。
  - **Codex subagents 负责多维度评判与 bad case 分析**：给出 0/1 分、百分制分数、失败原因、prompt 逻辑漏洞和规则级优化建议。
- CLI 不调用 Codex，也不生成新的 prompt。
- Prompt 优化不能通过向 prompt 追加 bad case 列表实现；应定位现有提示词规则、边界、优先级和输出契约的漏洞。

## 职责边界

| 能力 | CLI | Codex master agent | Codex subagents |
|---|---|---|---|
| 读取 prompt 模板 | 是 | 是 | 只接收 master 粘贴的 prompt 文本 |
| 读取变量文件 | 是 | 是 | 否 |
| Mustache 渲染 | 是 | 可查看渲染结果 | 否 |
| 调用目标模型执行任务 | 是 | 否 | 否 |
| 生成候选 prompt | 否 | 是 | 否 |
| 多维度 Judge 打分 | 否 | 聚合 | 是 |
| 失败原因分析 | 只记录结构化结果 | 聚合、归纳 | 是 |
| 优化停止判断 | 计算指标 | 决策是否继续 | 提供风险判断 |
| 变量文件修改 | 否 | 默认否 | 否 |
| 迭代日志 | 否 | 是 | 否 |

## CLI 设计

- CLI 名称：`codex-prompt-opt`。
- CLI 是轻量评测执行器，不维护优化工作空间，不管理候选搜索，不改写 prompt。
- 核心命令：
  - `validate`：校验 prompt 模板、Mustache 变量、JSON 变量文件。
  - `render`：把 prompt + case variables 渲染为完整任务实例。
  - `run`：用目标模型执行所有 cases，生成模型输出。
  - `judge-pack`：把 case、rendered prompt、target output、rubric 打包成 Codex 可评审文件。
  - `ingest-judgement`：读取 Codex 写回的 judgement JSON，计算通过率与平均分，并可写出 enriched judgement。
  - `optimize-step`：执行一轮 `render -> run -> judge-pack`，只生成评测产物。
  - `finalize`：对 master 已选择的 prompt 写出 `best_prompt.md`、`summary.json`、`run_report.md`。
- 明确取消：
  - `propose` 命令。
  - CLI 内部 GEPA-lite prompt 改写。
  - 把失败样例 advice/rationale 追加进 prompt 的机制。
  - 依赖 `.prompt-opt/candidates.jsonl` 的候选搜索状态。

## Codex Skill 设计

- Skill 名称：`codex-prompt-optimizer`。
- Skill 是 CLI 的智能控制层、subagent 调度层和 prompt 改写层。
- Codex master 工作流：
  1. 读取用户给定 prompt 和变量 JSON。
  2. 调用 `codex-prompt-opt validate`。
  3. 调用 `codex-prompt-opt optimize-step` 生成目标模型输出和 judge pack。
  4. 读取 `judge_pack_<candidate_id>.json`。
  5. 读取 `references/judge-subagent-prompt.md`，由 master 拼装完整 subagent 提示词。
  6. 按 case 分片一次性并行启动最大可用数量的 subagents。
  7. 只向 subagent 暴露任务描述、当前 prompt、bad cases/cases under review；不暴露路径、链接、密钥、仓库历史或 master 私有分析。
  8. 收集 subagent 的多维度评分、失败标签、prompt 逻辑漏洞和规则级优化建议。
  9. 聚合为 `judgement_<candidate_id>.json` 并调用 `ingest-judgement`。
  10. 若未达阈值且预算未耗尽，master 基于当前 prompt 和 subagent 建议直接生成下一版 prompt。
  11. 记录 `.prompt-opt/optimization_log.jsonl`。
  12. 达标或预算耗尽后调用 `finalize`。
- Subagent 必须输出：
  - `binary_score`: 0 或 1。
  - `score_100`: 0-100。
  - 多维度子分。
  - 失败原因和失败标签。
  - prompt 逻辑漏洞定位。
  - 规则级、指导性优化建议。
- Subagent 不负责写下一版 prompt，不允许建议“增加 bad case”。

## 数据与产物

- 输入：
  - `prompt.md`：Mustache prompt 模板。
  - `task.json`：单文件多样例变量文件。
- CLI 中间产物：
  - `rendered_cases_<candidate_id>.jsonl`：每个 case 的完整渲染 prompt。
  - `target_outputs_<candidate_id>.jsonl`：目标模型输出。
  - `judge_pack_<candidate_id>.json`：给 Codex master 和 subagents 的评审材料。
  - `judgement_<candidate_id>.json`：Codex master 聚合后的评分与诊断。
- Skill/master 产物：
  - `subagent_reviews_<candidate_id>.json`：subagent 原始评审聚合。
  - `optimization_log.jsonl`：每代 prompt、优化建议、评测集结果、优化策略和产物路径。
  - `prompts/<candidate_id>.md`：master 生成的每一代 prompt。
- 输出：
  - `best_prompt.md`：优化后 prompt。
  - `run_report.md`：用户可读优化报告。
  - `summary.json`：机器可读结果，包括所选候选、最佳分数和通过率；迭代次数、模型配置摘要等由 master 日志补充。

## Implementation Notes

- 已落地首版代码实现，CLI 入口为 `codex-prompt-opt`。
- 当前设计中，CLI 不实现 LLM Judge，不调用 Codex，不生成下一版 prompt。
- Judge 由 Codex subagents 完成，master 通过文件协议聚合 judgement 结果。
- CLI 使用 DSPy 调用目标模型。
- 变量文件首版使用 JSON，且一个文件包含多个 cases。
- 评分同时保留 0/1 和 0-100 两种形式。
- 优化对象只限 prompt template；CLI 和 Skill 默认都不修改变量文件。
