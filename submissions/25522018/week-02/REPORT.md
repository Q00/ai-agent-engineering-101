# Week 02: ReAct vs Plan-then-Execute

## 1. Variant Definition

ReAct solves the task by alternating between 
reasoning and tool use. It observes the result 
of each tool call and decides the next action 
based on that result.

Plan-then-Execute first creates a plan and 
then follows the planned steps to solve the 
task. Therefore, planning and execution are 
separated.

Both harnesses were tested on the same task: 
finding which hour in `app.log` has the most 
ERROR lines. The expected answer was `14:00`.

## 2. Measurements

| Run | Harness           | Success | Tokens | 
Iterations | Interventions |
| --- | ----------------- | ------- | -----: | 
---------: | ------------: |
| 1   | ReAct             | O       |   4120 |          
2 |             0 |
| 2   | ReAct             | O       |   3732 |          
2 |             0 |
| 3   | ReAct             | O       |   3946 |          
2 |             0 |
| 4   | Plan-then-Execute | X       |   2585 |          
1 |             0 |
| 5   | Plan-then-Execute | O       |  30531 |         
10 |             0 |
| 6   | Plan-then-Execute | O       |  52988 |         
12 |             0 |

ReAct achieved a success rate of 3/3 (100%), 
while Plan-then-Execute achieved 2/3 (66.7%).

The average token usage was about 3,933 for 
ReAct and 28,701 for Plan-then-Execute. ReAct 
required an average of 2 iterations, while 
Plan-then-Execute required about 7.7 
iterations. Neither method required human 
intervention.

## 3. Interpretation

ReAct performed better for this task. It 
succeeded in all three runs while using fewer 
tokens and fewer iterations. Plan-then-Execute 
failed once and required much more computation 
in its two successful runs.

This task is relatively simple because the 
agent only needs to inspect the log file, find 
ERROR lines, compare their hours, and return 
the hour with the highest count. For this type 
of task, creating a separate plan adds 
unnecessary overhead. ReAct can directly use 
the available tools and respond to the 
observations.

Therefore, based on this experiment, ReAct was 
more efficient and reliable than 
Plan-then-Execute for the given log-analysis 
task.
