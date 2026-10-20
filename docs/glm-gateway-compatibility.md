# GLM company gateway compatibility

Configure the exact model ID published by the gateway, for example `GLM-V5.2`
or `GLM-V5.0`. Do not rename it to `glm-5.3` to bypass validation. Use the
company endpoint and credentials in Settings; neither is bundled with history.

The native commander and agent executor accept GLM 5.x aliases for thinking
with public observation history. GLM-5.3's mandatory thinking policy remains
specific to that model. Private reasoning is not replayed or saved as output.

Zhipu completion requests use SSE for both text and native tools. Tool call
fragments are assembled by call index, preserving actual IDs and arguments.
The shared OpenAI-compatible parser and ZCode wire forwarding also accept SSE
bodies returned as strings when a gateway ignores a non-streaming request.
Legacy `function_call`, JSON strings and `data`/`result` wrappers are supported.
Missing final markers, truncated arguments and unsolicited calls cannot become
successful execution. Ordinary final text remains supported; command fallback
must resolve to an existing tool with explicit arguments before dispatch.

A plain connectivity check proves reachability and text generation, not native
tool compatibility. After configuring the company model, ask MARS to list the
current project's research history. Verify an actual returned tool result, then
try an existing model with a two-epoch budget. Authentication, rate limiting,
network errors and incompatible output are shown separately in the chat.

Parser tests use authored envelope records and SDK chunk objects. They do not
simulate model or tool execution. Company gateway acceptance still requires a
real request from the company network.
