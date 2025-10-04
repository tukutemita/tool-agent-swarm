# System Specification

- The orchestrator coordinates PM and worker agents using LM Studio hosted LLMs.
- Each agent maintains isolated conversation history scoped by session ID.
- PM dynamically assigns roles to workers A/B/C for every user task.
- Workers consult `/state` documents when clarification is needed.
