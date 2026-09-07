# Tool design

I described `write_note` as writing text only within the working directory so the model knows both when to use it—when a user explicitly asks to save a result or note—and its safety boundary. Requiring `path` and `content` makes the destination and exact text explicit, reducing ambiguous tool calls and preventing the tool from writing outside the assignment workspace.
