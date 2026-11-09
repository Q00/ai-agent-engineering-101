# Week 02 Harness Comparison

## 1. Variant definition

Both variants used the same task, model, and tools. ReAct used one continuing conversation with full history and decided the next action after every tool observation. It terminated when the model returned no tool call, with an 8-step maximum. Plan-then-Execute first generated the entire plan as a JSON list, then executed the steps sequentially in a separate executor context. It allowed at most three tool rounds per step and one replan after an `OFF_PLAN` result.

Of the five harness axes, context/decision structure, termination, and error recovery differed. Tool granularity was held constant because both harnesses used the same `read_file(path)` and `count_pattern(path, pattern)` tools. Human intervention was also effectively constant: all tools were read-only, so interventions remained 0.

## 2. Measurements

The final controlled batch was runs 17-22:

| run | harness | success | tokens | iters | interventions | note |
|---:|---|:---:|---:|---:|---:|---|
| 17 | react | O | 3569 | 2 | 0 | |
| 18 | react | O | 3634 | 2 | 0 | |
| 19 | react | O | 3784 | 2 | 0 | |
| 20 | plan_exec | X | 777 | 1 | 0 | replans=0 |
| 21 | plan_exec | X | 58271 | 14 | 0 | replans=0 |
| 22 | plan_exec | O | 53949 | 15 | 0 | replans=1 |

ReAct succeeded in 3/3 runs, averaging 3,662 tokens and 2.0 iterations. Plan-then-Execute succeeded in 1/3 runs, averaging 37,666 tokens and 10.0 iterations. Earlier runs remain in `results.csv` and `logs/` as process evidence: runs 1-12 were authentication failures, and runs 13-16 were a preliminary batch interrupted before completion.

## 3. Interpretation

For this small log-analysis task, ReAct won both reliability and efficiency. Its termination axis let it stop as soon as the answer was available, while its observation-driven loop reconsidered the next action after each tool result. Plan-then-Execute introduced extra failure points through its planning and fixed-step execution structure. In run 20, the planner returned `read_file(path="app.log")` instead of the required JSON list, so execution never started. In run 21, the executor had already produced the correct `14:00` answer at step 2 but continued through the remaining plan and finally returned an empty final answer, using 58,271 tokens. Error recovery did help in run 22: after exceeding the per-step tool-call budget, `OFF_PLAN` triggered the allowed replan and the run recovered to the correct answer. However, that flexibility required 15 iterations and 53,949 tokens. Thus, for this task, ReAct's adaptive termination reduced cost and failure opportunities, while Plan-then-Execute's explicit planning and bounded replanning added overhead that was not useful for such a short task.

### Reproducibility

Provider: OpenRouter  
Model: `nvidia/nemotron-3.5-lightning:free`  
Tools: `read_file(path)` and `count_pattern(path, pattern)`  
Python: 3.14.7

Set `OPENAI_BASE_URL=https://openrouter.ai/api/v1`, set `OPENAI_API_KEY` locally, set `AGENT_MODEL=nvidia/nemotron-3.5-lightning:free`, and run:

`python run_ab.py --runs 3`
