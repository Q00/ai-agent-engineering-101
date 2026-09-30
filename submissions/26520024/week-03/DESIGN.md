# Frozen experiment design

This file and tasks.json are committed before the first live model call.
Author/student: 26520024, LeeUichann. Implementation assistance: Codex.

## Question and scope

Does self-reported LLM confidence allocate diffusion-image safety review requests
to the intended specialist? Six synthetic text descriptions, two gold labels each:
A = sexual_safety, B = violence_safety, C = ip_safety. Gold is the intended
specialist, NOT a harmful/safe image label. No images, actual detector inference,
downstream review, or legal infringement judgments are performed.

## Controlled conditions

Exact prompts are in prompts.json. All calls use opaque names A/B/C, preventing
the name itself from retaining the specialty in the homogeneous condition.
Baseline uses three skills; homogeneous changes only the skill string to the
same generalist string; overconfident changes only C's baseline prompt by
appending the single provided sentence. Contractor order is always A, B, C.
Tasks always run T01 through T06. Three repetitions, each with baseline,
homogeneous, overconfident in that order: 9 runs, 162 intended model calls.
This fixed run order can confound temporal service effects; it is not randomized.

## Backend and scoring

OpenAI via authenticated Codex CLI, gpt-6-astra, reasoning effort low. Python
3.8.19 in existing conda base; standard library only. No packages installed.
Temperature and max output tokens cannot be set by this adapter; internal
values are unknown, not assumed to be zero. Identical settings across conditions.
Each bid uses a fresh ephemeral CLI invocation in an empty scratch directory,
with user config ignored and native tools disabled. System/user roles are
embedded in a fixed CLI adapter request, not sent as direct API role messages.
No gold labels, previous bids, or repository documents are sent to contractors.

JSON must contain exactly bid (boolean), confidence (finite number 0..100),
reason (nonempty string). Duplicate keys, NaN, code fences, extra prose/fields,
or wrong types are invalid. Invalid responses count as no bid and parse_fails;
no repair or resampling. Valid bid=false is a decline, not a parse failure.
Highest confidence among valid positive bids wins; ties favor the earliest
respondent (A before B before C), per the lecture. No threshold or gold-based
filter. No positive bids means unassigned. Gold is used only after awarding.

Course messages = 3 announcements per task + valid positive bids + awards.
Declines and malformed responses are logged but not counted as bid messages,
matching week-03.html's manager example. This is NOT all network traffic or
model calls. Also record calls, declines, parse failures, tokens, and wall time.
For complete runs: correct + misawards + unassigned = 6.

## Evidence and failures

Append one CSV row and immutable console log per attempt. Log exact inputs,
raw Codex events, raw final text, all announcements/bids/awards and evaluation.
Transport failure, timeout (180 seconds), or a native tool action crashes that
run; retain its partial log and a CSV row with blank counts and error note.
Stop the batch on a crash so configuration failures are not repeated blindly.
Resume appends a fresh run for the unfinished slot; failed rows are never erased.
Unit tests use fake replies in temporary directories, never submitted results.
Offline validation reconstructs counts and winners from live evidence.

## Interpretation limits

Prompt-assigned skills are not verified model competence. Homogeneous gold
matching measures preservation of predefined ownership, not generalist quality.
Confidence is not calibrated probability. Six easy, unambiguous tasks and three
repeats per condition do not establish general reliability. Fixed tie order is
an explicit bias. Report observed outcomes, even if overconfidence does not win.
Do not tune prompts/tasks/gold after seeing results. Do not push or create a PR
until the student reviews the completed local experiment.
