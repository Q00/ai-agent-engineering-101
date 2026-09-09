# AI Agent Engineering 101

Seoul National University of Science and Technology, Fall 2026. This repository holds the lecture notes and is where all coursework is submitted.

Instructor: Jaegyu Lee ([@Q00](https://github.com/Q00))

> If this repository is useful to you, please **star it** ⭐ before you fork — it helps other students and future cohorts find it.

## Lecture notes

`index.html` is the course home; `week-NN.html` is that week's note. Weeks 01 through 03 are up; the rest are published as the semester goes on.

## How submission works: fork and PR

Every submission in this course is a GitHub pull request. Two reasons:

1. **PR timestamps cannot be forged.** Commit times can be rewritten locally, but the time a PR was opened is recorded by GitHub's servers. Deadlines are judged by PR open time.
2. **Commit history is evidence of process.** This course grades the process as much as the result. What you tried and what you threw away must be visible in the history.

### One-time setup

```bash
# 1. Fork this repository (Fork button on GitHub)
# 2. Clone your fork
git clone https://github.com/<your-id>/ai-agent-engineering-101.git
cd ai-agent-engineering-101
git remote add upstream https://github.com/Q00/ai-agent-engineering-101.git
```

### Every week

```bash
git fetch upstream && git merge upstream/main   # pull the latest assignment
git switch -c week-01                           # one branch per week
# ... work ...
git push origin week-01
# Open a PR to upstream. Title: [week-01] 23512345
```

## Repository layout

```
index.html         Course home. week-NN.html is that week's lecture note.
roster/            One file per student. Created in the week-01 lab.
weeks/week-NN/     Assignment specs and starter code. Read-only.
submissions/       Your work. submissions/<student-id>/week-NN/ is your territory.
scripts/           The same checks CI runs, runnable locally.
```

One rule: **do not touch anything outside `submissions/<your-student-id>/`.** PRs that modify `weeks/` or another student's submission directory are rejected by the checks.

## Automated checks

Opening a PR triggers structural checks on GitHub Actions: the code parses, required files exist, and the spec (e.g. tool count) is met. Run the same checks locally before pushing:

```bash
python scripts/check_week01.py submissions/<student-id>/week-01
```

Passing checks is the minimum bar for submission, not a grade. Actual grading — does the code solve a real task, is the process honestly recorded — is done by a human.

## Commit rules

- **Do not squash.** Leave broken intermediate commits as they are. A messy history is not a penalty; it is grading evidence.
- Commit run logs (console output) to `submissions/<student-id>/week-NN/logs/`.
- Never commit API keys. Environment variables only.

## LLM policy

LLMs and agent tools (Claude Code, Codex, opencode, anything) are free to use on every assignment. `AGENTS.md` and `CLAUDE.md` in this repository exist so those tools understand the rules here. The one condition: what you asked for and what you discarded must remain in the commit history and logs.
