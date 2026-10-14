# Week 02: ReAct vs Plan-then-Execute

## 1. Experiment Setup

This experiment compares two agent harnesses on the same task:

> In `app.log`, which hour (`HH:00`) has the most `ERROR` lines?

The expected answer was fixed as `14:00`.

Both harnesses used the same model and the same tools so that the main experimental difference was the harness design.

### Provider and model

- Provider: OpenRouter through the OpenAI-compatible API
- Model: `nvidia/nemotron-3.5-lightning:free`
- Model configuration: `AGENT_MODEL` environment variable
- API endpoint: OpenRouter OpenAI-compatible endpoint through `OPENAI_BASE_URL`

The model is selected by `tools_shared.py` from the environment. The same configuration was used for both ReAct and Plan-then-Execute.

### Tool schema

Both harnesses used the same two read-only tools.

```text
read_file(path: string)
```

Reads the first 4000 characters of a text file in the working directory.

```text
count_pattern(path: string, pattern: string)
```

Counts lines in a text file that match a regular expression.

Because both tools are read-only, no human approval was required during the experiment.

### Execution

The experiment was run from the Week-02 submission directory using:

```bash
python run_ab.py --runs 3
```

`run_ab.py` reads the task and expected answer from `TASK.md`, executes each harness three times, judges whether the expected string occurs in the final answer, stores every run in `results.csv`, and saves the console trace under `logs/`.

Failed runs were retained as experimental data.

## 2. Harness Design

### ReAct

ReAct repeatedly follows a Thought → Action → Observation loop. After receiving the result of a tool call, the model decides what to do next.

### Plan-then-Execute

Plan-then-Execute first asks the model to generate the complete plan as a JSON list. A separate executor then performs the plan one step at a time.

If a step returns `OFF_PLAN`, the harness permits at most one replan.

## 3. Measurements

| Run | Harness | Success | Tokens | Iterations | Interventions |
| --- | --- | --- | ---: | ---: | ---: |
| 1 | ReAct | O | 4,120 | 2 | 0 |
| 2 | ReAct | O | 3,732 | 2 | 0 |
| 3 | ReAct | O | 3,946 | 2 | 0 |
| 4 | Plan-then-Execute | X | 2,585 | 1 | 0 |
| 5 | Plan-then-Execute | O | 30,531 | 10 | 0 |
| 6 | Plan-then-Execute | O | 52,988 | 12 | 0 |

ReAct succeeded in 3/3 runs (100%), while Plan-then-Execute succeeded in 2/3 runs (66.7%).

Average token usage was approximately 3,933 for ReAct and 28,701 for Plan-then-Execute.

ReAct required 2 model calls per run on average. Plan-then-Execute required approximately 7.7 model calls per run.

No run required human intervention.

## 4. Analysis by the Five Design Axes

### Axis 1: Context management

ReAct keeps the full interaction history in one conversation. Each new decision therefore has access to the previous tool calls and observations.

Plan-then-Execute separates the initial planner from the executor. It requires an additional planning model call before tool execution begins and then sends individual plan steps to the executor.

For this small log-analysis task, the separate planning stage did not reduce the amount of execution. The two successful Plan-then-Execute runs required 10 and 12 model calls, compared with 2 calls in every ReAct run. This additional interaction also contributed to substantially higher token usage.

### Axis 2: Tool granularity

Tool granularity was controlled rather than varied: both harnesses used exactly the same `read_file(path)` and `count_pattern(path, pattern)` tools.

Therefore, the large difference in tokens and iterations cannot be attributed to one harness receiving a more powerful tool set. The comparison instead reflects differences in how the harnesses organize planning and tool use.

### Axis 3: Termination

ReAct terminates when the model returns a response without another tool call, with `max_steps=8` as a safety cap.

Plan-then-Execute instead creates a plan and executes its steps before explicitly requesting the final answer.

For this task, ReAct terminated after only 2 model calls in all three runs. The successful Plan-then-Execute runs required 10 and 12 calls. Thus, the more structured termination path of Plan-then-Execute corresponded to more iterations for this relatively simple task.

### Axis 4: Error recovery and flexibility

The clearest failure occurred in `plan_exec-04.txt`:

```text
[plan] not valid JSON: ''
[final] plan parse failed
[judge] expected='14:00' -> X (417.4s)
```

The initial planner returned an empty response instead of the required JSON list. `parse_plan()` therefore failed and the harness terminated before executing the plan.

This is important because the run did not fail from an incorrect analysis of `app.log`. It failed at the interface between the planning stage and execution stage.

Plan-then-Execute supports one replan after an `OFF_PLAN` result, but this mechanism cannot recover from an invalid initial plan because execution has not started yet.

ReAct does not require an initial structured JSON plan. Tool errors are instead returned to the same conversation as observations, allowing the model to react on its next iteration.

Therefore, in this experiment, the structured planner introduced an additional failure point that affected the observed success rate.

### Axis 5: Human intervention

Both available tools are read-only. The `IRREVERSIBLE` set is therefore empty, and neither harness required human approval.

All six runs recorded zero interventions.

This axis did not explain the observed difference in success rate, token usage, or iterations.

## 5. Interpretation

For this specific task, ReAct completed all three runs successfully with substantially fewer tokens and model calls.

Plan-then-Execute succeeded in two of three runs, but its successful runs required much more interaction. Its failed run also demonstrates a failure mode associated with the explicit planning interface: the planner must produce valid JSON before execution can begin.

The result should not be interpreted as showing that ReAct is always preferable to Plan-then-Execute. The task is small and reactive: the agent only needs to inspect a log, count matching lines, compare hours, and return one value. A more complex multi-stage task may benefit differently from explicit planning.

For this experiment, however, the extra planning structure increased the observed token and iteration costs and introduced a plan-parsing failure point without providing an observable benefit on the tested task.

## 6. Task Definition and Commit History Note

`TASK.md` contains the task and the fixed expected answer used by `run_ab.py`. The runner reads both values before executing the harnesses, and the success criterion checks whether the expected value appears in the final answer.

In the submitted Git history, `TASK.md` and the execution results were committed together. Therefore, the repository history alone does not provide a separate earlier commit proving that the task definition was committed before execution.

I am not rewriting or modifying the existing history. The submitted logs and `results.csv` are retained as the original execution records, and this note documents the limitation of the commit history transparently.