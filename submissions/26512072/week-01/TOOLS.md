# Added Tool: clock

I added a `clock` tool that returns the current local date and time. I described the tool as being used only when the user's request depends on the actual current date or time. This description was chosen to make the tool-selection boundary explicit: the agent should call `clock` for current-time questions, while continuing to use `calculator` for arithmetic and `read_file` for file-reading tasks.

## Model

- Provider: OpenRouter
- Model: `minimax/minimax-m3:free`

## Environment

The API key is provided through the `OPENROUTER_API_KEY` environment variable and is not stored in the repository.

## Run

```powershell
$env:OPENROUTER_API_KEY="YOUR_KEY"
python submissions/26512072/week-01/first_agent.py
```
