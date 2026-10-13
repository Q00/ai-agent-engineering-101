# New Tool: write_note

I added the `write_note` tool because I wanted the agent to save the result of its work to a file instead of only showing the result on the screen. I described the tool as "Save text to a file in the current working directory" because this clearly tells the model what the tool does and also limits where it can write. The tool requires both `path` and `content` because the agent must decide where to save the result and what information should be written.
