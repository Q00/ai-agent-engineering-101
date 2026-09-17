# week-01 - first agent with three tools

Third tool added: **`clock`**. See `TOOLS.md` for why its description is worded
the way it is, and what changed when it was rewritten.

## Files

| File | What it is |
|---|---|
| `first_agent.py` | The agent. Three tools: `calculator`, `read_file`, `clock`. |
| `TOOLS.md` | The design argument for the `clock` description, and the run-01 vs run-02 comparison. |
| `notes.txt` | Input file the agent reads. |
| `logs/` | Console captures. `run-01` is the one-line description, `run-02` the explicit one. |

## How to run

```bash
pip install openai tzdata     # tzdata is required on Windows: no IANA db otherwise
```

Windows cmd (no quotes, no spaces around `=`):

```
set OPENROUTER_API_KEY=<your openrouter key>
python first_agent.py > logs\run-01.txt 2>&1
```

bash:

```bash
export OPENROUTER_API_KEY=<your openrouter key>
python first_agent.py 2>&1 | tee logs/run-01.txt
```

A different goal can be passed as the first argument:

```
python first_agent.py "What day of the week is 2026-12-25?"
```

## Settings (everything except the key)

| | |
|---|---|
| Model | `minimax/minimax-m3:free` (override with `AGENT_MODEL`) |
| Endpoint | `https://openrouter.ai/api/v1`, OpenAI-compatible (override with `OPENAI_BASE_URL`) |
| Key | read from `OPENROUTER_API_KEY`, falling back to `OPENAI_API_KEY` |
| Loop bound | `max_steps=8` model turns |
| Sampling | not set; the provider default applies, so runs are not bit-identical - the tool-call sequence is what reproduces |
| 429 handling | own backoff, 5s / 15s / 45s, logged; the SDK's own retries are off (`max_retries=0`) so every 429 is visible |
| Timezone | `clock` defaults to `Asia/Seoul`, so `day_number` does not depend on where the code runs |
| Default goal | `Read notes.txt, work out how much is still unpaid, and tell me how many days have passed between the memo date and today.` |

## Expected result

From `notes.txt`: unpaid = `48000 + 9500 - 12000` = **45500**.
The number of days depends on when it runs, which is the point of the tool -
on 2026-09-07 it is `739866 - 739860` = 6.

## Two things that cost me a run

- **Model choice.** `google/gemma-4-26b-a4b-it:free` returned HTTP 429
  (`limit_source: upstream_provider_shared_pool`) rather than a tool-support
  error, so the model does support tool calling - the free pool was simply
  saturated. Rather than wait it out I added visible backoff and switched to
  `minimax/minimax-m3:free`.
- **Console encoding.** The model writes U+2212 MINUS SIGN, and a Windows
  console is cp949 here, so `print()` raised `UnicodeEncodeError` and the final
  answer never reached the log. `first_agent.py` now forces utf-8 on stdout and
  stderr. Note that `type logs\run-02...txt` still shows `??` for that
  character - the file is fine, the console codepage is not; `chcp 65001`
  first if you want to read it in place.

## Structure check

```bash
python scripts/check_week01.py submissions/26512071/week-01
```
