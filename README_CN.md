# Prompt Evolver

英文文档：[README.md](README.md)

Prompt Evolver 是一个本地提示词优化工具链，用于基于文件交接的 prompt 评估工作流。CLI 负责执行可重复的自动化流程：校验 Mustache prompt 模板，将 JSON 中的多个 case 渲染成任务实例，调用配置好的目标模型，把目标模型输出打包给结构化评审流程，读取结构化评分结果，并写出选定 prompt 的最终产物。

CLI 负责确定性的文件处理和目标模型执行步骤。Prompt 重写和评审在 CLI 外完成：读取生成的 judge pack，并按约定写入 JSON 结果。

## 核心能力

- 使用单个 JSON 文件中的多个评估 case 渲染 Mustache prompt 模板。
- 将每个渲染后的 prompt 视为一个任务实例，也可以称为 rendered prompt、prompt instantiation 或 evaluation case/example。
- 将一个变量文件确定性划分为训练集和测试集，默认按 `expected.ground_truth` 分层抽样，比例为 70% / 30%。
- 在 `run` 和 `optimize-step` 中调用配置好的目标模型。
- 通过 JSON 文件交换结构化评审结果，评审流程不耦合进 CLI。
- prompt 生成不放在 CLI 中；根据评审结论在两轮评估之间编辑 prompt 模板。
- 只优化 prompt template；CLI 不会重写变量文件，也不会把 bad case 追加到 prompt 中。
- prompt 迭代只使用训练集，最终通过 `test-step` 对 held-out 测试集做一次准确率评估。
- prompt 迭代后可用 `prompt-diff` 打开本地左右并列 diff 审阅页面。
- 支持按阈值和预算停止：目标通过率、目标平均 `score_100`、最大迭代预算。

## 环境要求

- macOS 或其他类 Unix shell 环境。
- Python 3.10 或更高版本。
- 真实目标模型运行需要配置模型参数。

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

- `MODEL_NAME`：目标模型标识。
- `MODEL_API_BASE`：可选 API base URL。
- `MODEL_API_KEY`：默认 API key 环境变量。
- `MODEL_TEMPERATURE`：可选目标模型 temperature。
- `MODEL_MAX_TOKENS`：可选最大 token 预算。
- `MODEL_TIMEOUT_SECONDS`：可选目标模型请求超时时间。
- `MODEL_ENABLE_THINKING`：可选布尔值，会作为 `extra_body.enable_thinking` 传给兼容的 OpenAI 风格后端。

目标模型执行前，CLI 会自动读取当前工作目录下的 `.env`。本仓库中的 `.env*` 文件只放占位值，真实密钥应保留在本地。

当设置了 `MODEL_API_BASE` 且 `MODEL_NAME` 没有 provider 前缀时，CLI 会把它视为 OpenAI 兼容模型，并在内部使用 `openai/<MODEL_NAME>`。

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
prompt-evolver config set MODEL_NAME DeepSeek-V4-Pro
prompt-evolver config set MODEL_API_BASE https://example.com/v1
prompt-evolver config set MODEL_API_KEY sk-...
prompt-evolver config set MODEL_TEMPERATURE 0.1
prompt-evolver config set MODEL_MAX_TOKENS 2048
prompt-evolver config set MODEL_TIMEOUT_SECONDS 90
prompt-evolver config set MODEL_ENABLE_THINKING true
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

如果没有显式提供训练集和测试集，先创建默认划分。划分过程是确定性的，使用 `--train-ratio 0.7`，并按 `expected.ground_truth` 分层抽样：

```bash
prompt-evolver split examples/task.example.json --train-out .prompt-evolver/train.json --test-out .prompt-evolver/test.json
```

用仓库内置样例 prompt 校验训练集：

```bash
prompt-evolver validate examples/prompt.example.md .prompt-evolver/train.json
```

最终评估前，只允许对测试文件做格式校验；不要用测试 case 或 expected output 参与 prompt 迭代：

```bash
prompt-evolver validate examples/prompt.example.md .prompt-evolver/test.json
```

渲染训练任务实例：

```bash
prompt-evolver render examples/prompt.example.md .prompt-evolver/train.json --out .prompt-evolver/rendered_cases.jsonl
```

在训练集上运行目标模型：

```bash
prompt-evolver run .prompt-evolver/rendered_cases.jsonl --out .prompt-evolver/target_outputs.jsonl --model "$MODEL_NAME"
```

打包结构化评审材料：

```bash
prompt-evolver judge-pack .prompt-evolver/rendered_cases.jsonl .prompt-evolver/target_outputs.jsonl .prompt-evolver/train.json --out .prompt-evolver/judge_pack.json
```

写入 `judgement.json` 后，读取评分结果：

```bash
prompt-evolver ingest-judgement .prompt-evolver/judgement.json --out-dir .prompt-evolver
```

执行一轮自动目标模型评估并生成 judge pack：

```bash
prompt-evolver optimize-step examples/prompt.example.md .prompt-evolver/train.json --out-dir .prompt-evolver --candidate-id initial --model "$MODEL_NAME"
```

仓库内置样例文件是 `examples/prompt.example.md` 和 `examples/task.example.json`。本地工作输入 `examples/prompt.md` 和 `examples/task.json` 已被忽略，真实 prompt 和评估数据可以保留在本机。

CLI 不生成下一版 prompt。根据评审结论编辑 prompt 模板，在 `.prompt-evolver/optimization_log.jsonl` 中记录迭代，然后用新版 prompt 再次运行 `optimize-step`。

写出选定的最终 prompt：

```bash
prompt-evolver finalize .prompt-evolver/prompts/best.md .prompt-evolver/judgement_best.json --out-dir .prompt-evolver/final
```

训练达到停止条件或迭代预算耗尽后，对 held-out 测试集只运行一次准确率评估：

```bash
prompt-evolver test-step .prompt-evolver/final/best_prompt.md .prompt-evolver/test.json --out-dir .prompt-evolver --candidate-id final_test --model "$MODEL_NAME"
```

如果目标模型输出已经存在，可以直接评分：

```bash
prompt-evolver score-accuracy .prompt-evolver/test.json .prompt-evolver/target_outputs_final_test.jsonl --out .prompt-evolver/accuracy_final_test.json
```

Prompt 迭代结束后，打开浏览器审阅页，对比输入 prompt 和最终 prompt：

```bash
prompt-evolver prompt-diff examples/prompt.md output/trace_1782302086/final/best_prompt.md
```

该命令会以前台方式启动本地服务，尽量自动打开浏览器，打印审阅 URL，并在按下 `Ctrl+C` 后关闭服务。如果默认端口被占用，CLI 会自动尝试后续可用端口。

## Skill 用法

Skill 位于 `skills/prompt-evolver`。它围绕 CLI 提供可重复工作流：输入校验、单轮目标模型评估、judge pack 评审、prompt 迭代和最终产物写出。

可以直接使用这些简短提示词：

- 数据划分：`使用 $prompt-evolver 按默认分层 70/30 方法，将 examples/task.example.json 划分为训练集和测试集。`
- 输入校验：`使用 $prompt-evolver 在训练模型调用前校验 examples/prompt.example.md 和 .prompt-evolver/train.json。`
- 单轮评估：`使用 $prompt-evolver 对 examples/prompt.example.md 和 .prompt-evolver/train.json 运行一轮 optimize-step，并把 judge pack 保存到 .prompt-evolver。`
- 输出评审：`使用 $prompt-evolver 审阅 judge pack，逐 case 打分，并按约定 schema 写入 judgement JSON。`
- 改进 prompt：`使用 $prompt-evolver 总结失败 case，更新 prompt 模板，并把本轮迭代记录到 .prompt-evolver/optimization_log.jsonl。`
- 最终产物：`使用 $prompt-evolver 将选定 prompt 和 judgement 写出到 .prompt-evolver/final。`
- Held-out 测试：`使用 $prompt-evolver 对 .prompt-evolver/test.json 只运行一次 test-step，并报告 .prompt-evolver/accuracy_final_test.json。`
- Prompt diff 审阅：`运行 prompt-evolver prompt-diff examples/prompt.md output/trace_1782302086/final/best_prompt.md，然后引导用户打开打印出来的 URL 审阅左右并列 prompt diff。`

## 文档维护

`README.md` 和 `README_CN.md` 应保持语义同步。以后修改任意一份 README 时，应在同一任务中把面向用户的同等变更同步到另一份 README。

## 目录结构

```text
src/prompt_evolver/        CLI 实现
tests/                     单元测试
examples/prompt.example.md 仓库内置 prompt 样例
examples/task.example.json 仓库内置 JSON 变量样例
examples/prompt_jxb_v*.md  JXB prompt 迭代历史
skills/prompt-evolver/     该工作流使用的 Skill
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

真实模型执行前，请先配置 `MODEL_NAME` 和凭据。
