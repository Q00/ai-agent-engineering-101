# AGENTS.md

Context for coding agents (Claude Code, Codex, opencode, and friends) working in this repository. As the course teaches: a tool's description is its interface. This file is the interface of this repository.

## What this repository is

The assignment submission repository for the Agentic AI course at SeoulTech. Students work in their forks and submit by opening PRs to upstream. Grading is half reproducibility (does the code actually run) and half process honesty (are the attempts and discards visible in history).

## Invariants (CI rejects PRs that break these)

1. Write only inside `submissions/<student-id>/`. The user's student id can be looked up from their GitHub id in `roster/`.
2. `weeks/`, `roster/` (except the user's own file), `scripts/`, `.github/`, and other students' `submissions/` directories are read-only.
3. Never commit API keys, tokens, or `.env` files.
4. Never squash commits or rewrite history. Failed attempts are grading evidence; preserve them.

## Repository map

```
index.html, week-NN.html        Lecture notes. Read-only.
roster/<student-id>.md          Student-id to GitHub-id mapping. Created in week-01.
weeks/week-NN/README.md         The assignment spec for that week. Source of truth for requirements.
weeks/week-NN/starter/          Starter code (weeks 01 and 02 only). Copy it into submissions/ to begin.
submissions/<student-id>/week-NN/   Where the work lives.
scripts/check_weekNN.py         The same checks CI runs. Run locally first.
```

## Weekly workflow (identical every week)

1. Read `weeks/week-NN/README.md` for the submission requirements.
2. Copy `weeks/week-NN/starter/` to `submissions/<student-id>/week-NN/`. From week-03 on there is no starter: the README states the data contract and the code layout is the student's.
3. Do the work. **Commit after every unit of work** — each tool added, each fix, each failed attempt gets its own commit. Never batch a whole session into one commit. Save agent run console output as files under `submissions/<student-id>/week-NN/logs/`.
4. Before submitting, run: `python scripts/check_weekNN.py submissions/<student-id>/week-NN`
5. When checks pass, commit and push. PR title format: `[week-NN] <student-id>`

## How to verify

- Syntax: `python -m py_compile <file>`
- Spec: `scripts/check_weekNN.py` (the exact script CI runs)
- Execution: agent code needs an API key. If no key is in the environment, skip execution checks and tell the user. Never ask for or hardcode a key.

## Do not

- Solve the core learning objective of an assignment wholesale. Grading looks at process, so code the user does not understand ultimately hurts them. Explain the blocker and offer options instead.
- Modify files outside `submissions/`. Beyond CI rejection, it is the shared area of a course repository.
- Edit or tidy log files. Logs are valuable exactly as they are.
