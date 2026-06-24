# Prompt Evolver

英文文档：[README.md](README.md)

Prompt Evolver 是一个本地提示词优化工具链，用于 Codex 主导的工作流。CLI 负责执行可重复的自动化流程：校验 Mustache prompt 模板，将 JSON 中的多个 case 渲染成任务实例，通过 DSPy 调用目标模型，把目标模型输出打包给 Codex Judge 审阅，读取结构化评分结果，并写出 Codex 选定 prompt 的最终产物。

Codex 不作为目标模型执行器。Codex 负责阅读目标模型输出，派发 Judge 子代理进行多维评分和 bad case 分析，写入结构化评分与失败诊断，以 master agent 身份重写 prompt 模板，并调用 CLI 进入下一轮评估。

## 核心能力

- 使用单个 JSON 文件中的多个评估 case 渲染 Mustache prompt 模板。
- 将每个渲染后的 prompt 视为一个任务实例，也可以称为 rendered prompt、prompt instantiation 或 evaluation case/example。
- 在 `run` 和 `optimize-step` 中通过 DSPy 调用目标模型。
- 通过 JSON 文件交换 Codex Judge 结果，CLI 不直接调用 Codex。
- prompt 生成不放在 CLI 中；Codex master agent 根据子代理建议重写 prompt 模板。
- 只优化 prompt template；CLI 不会重写变量文件，也不会把 bad case 追加到 prompt 中。
- 支持 master agent 按阈值和预算停止：目标通过率、目标平均 `score_100`、最大迭代预算。

## 环境要求

- macOS 或其他类 Unix shell 环境。
- Python 3.10 或更高版本。
- 真实目标模型运行需要配置 DSPy 兼容的模型参数。

## 安装

以 editable 模式安装包：

```bash
python3 -m pip install -e .
```

如果创建虚拟环境，安装前先激活：

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e .
```

如果虚拟环境目录名为 `venv`，使用：

```bash
source venv/bin/activate
```

## 配置

CLI 从命令选项或环境变量读取模型配置：

- `DSPY_MODEL`：传给 `dspy.LM` 的目标模型标识。
- `DSPY_API_BASE`：可选 API base URL。
- `DSPY_API_KEY`：默认 API key 环境变量。
- `DSPY__TEMPERATURE`：可选目标模型 temperature。
- `DSPY__MAX_TOKENS`：可选最大 token 预算。
- `DSPY__TIMEOUT_SECONDS`：可选目标模型请求超时时间。
- `EVO_EVAL_ENABLE_THINKING`：可选布尔值，会作为 `extra_body.enable_thinking` 传给兼容的 OpenAI 风格后端。

目标模型执行前，CLI 会自动读取当前工作目录下的 `.env`。本仓库中的 `.env*` 文件只放占位值，真实密钥应保留在本地。

当设置了 `DSPY_API_BASE` 且 `DSPY_MODEL` 没有 provider 前缀时，CLI 会把它视为 OpenAI 兼容模型，并将 `openai/<DSPY_MODEL>` 传给 DSPy/LiteLLM。

查看当前模型配置：

```bash
prompt-evolver config show
```

创建首次使用的本地配置文件：

```bash
prompt-evolver config init
```

更新模型参数：

```bash
prompt-evolver config set DSPY_MODEL DeepSeek-V4-Pro
prompt-evolver config set DSPY_API_BASE https://example.com/v1
prompt-evolver config set DSPY_API_KEY sk-...
prompt-evolver config set DSPY__TEMPERATURE 0.1
prompt-evolver config set DSPY__MAX_TOKENS 2048
prompt-evolver config set DSPY__TIMEOUT_SECONDS 90
prompt-evolver config set EVO_EVAL_ENABLE_THINKING true
```

## 输入格式

Prompt 模板使用 Mustache 语法：

```mustache
Classify the support request.

Request: {{request}}

Return JSON with fields: label, confidence, rationale.
```

变量文件是一个包含多个 case 的 JSON 文件：

```json
{
  "task": {
    "name": "Support classifier",
    "rubric": "The label must match expected.label and the response must be valid JSON."
  },
  "cases": [
    {
      "id": "refund_001",
      "variables": {
        "request": "I was charged twice and need a refund."
      },
      "expected": {
        "label": "billing"
      }
    }
  ]
}
```

## CLI 工作流

校验仓库内置样例输入：

```bash
prompt-evolver validate examples/prompt.example.md examples/task.example.json
```

渲染任务实例：

```bash
prompt-evolver render examples/prompt.example.md examples/task.example.json --out .prompt-evolver/rendered_cases.jsonl
```

运行目标模型：

```bash
prompt-evolver run .prompt-evolver/rendered_cases.jsonl --out .prompt-evolver/target_outputs.jsonl --model "$DSPY_MODEL"
```

打包 Codex Judge 材料：

```bash
prompt-evolver judge-pack .prompt-evolver/rendered_cases.jsonl .prompt-evolver/target_outputs.jsonl examples/task.example.json --out .prompt-evolver/judge_pack.json
```

Codex 写入 `judgement.json` 后，读取评分结果：

```bash
prompt-evolver ingest-judgement .prompt-evolver/judgement.json --out-dir .prompt-evolver
```

执行一轮自动目标模型评估并生成 judge pack：

```bash
prompt-evolver optimize-step examples/prompt.example.md examples/task.example.json --out-dir .prompt-evolver --candidate-id initial --model "$DSPY_MODEL"
```

仓库内置样例文件是 `examples/prompt.example.md` 和 `examples/task.example.json`。本地工作输入 `examples/prompt.md` 和 `examples/task.json` 已被忽略，真实 prompt 和评估数据可以保留在本机。

CLI 不生成下一版 prompt。Codex master agent 聚合子代理建议，直接编辑 prompt 模板，在 `.prompt-evolver/optimization_log.jsonl` 中记录迭代，然后用新版 prompt 再次运行 `optimize-step`。

写出 Codex 选定的最终 prompt：

```bash
prompt-evolver finalize .prompt-evolver/prompts/best.md .prompt-evolver/judgement_best.json --out-dir .prompt-evolver/final
```

## Codex Skill

Codex Skill 位于 `skills/prompt-evolver`。当 Codex 需要编排该工作流、派发并行 Judge 子代理、聚合目标模型输出评分、写入 CLI 消费的结构化 `judgement.json`、以 master agent 身份重写 prompt、维护迭代日志时，应使用该 Skill。

## 文档维护

`README.md` 和 `README_CN.md` 应保持语义同步。以后修改任意一份 README 时，应在同一任务中把面向用户的同等变更同步到另一份 README。

## 目录结构

```text
src/prompt_evolver/        CLI 实现
tests/                     单元测试
examples/prompt.example.md 仓库内置 prompt 样例
examples/task.example.json 仓库内置 JSON 变量样例
examples/prompt_jxb_v*.md  JXB prompt 迭代历史
skills/prompt-evolver/     该工作流使用的 Codex Skill
README.md                  英文文档
README_CN.md               中文文档
PLAN.md                    原始设计方案
```

## 开发与测试

运行不依赖外部模型调用的单元测试：

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```

运行语法编译检查：

```bash
python3 -m compileall src tests
```

真实模型执行前，请先配置 `DSPY_MODEL` 和凭据。
