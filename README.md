# cloakai

`cloakai` is an agent orchestration runtime for managing, compiling, and exposing custom AI agents across multiple target environments.

You define agents declaratively — giving each one a persona, rules, skills, and workflows — and cloakai handles the rest: compiling your configuration into the format each target platform expects, and exposing your agents as callable tools via an MCP server.

## What it does

**Compile once, deploy everywhere.** Different AI platforms have different conventions. Some support modular skills and agent files. Others only accept a single system prompt. cloakai understands these differences and compiles your agent definitions into the right format for each target automatically.

**Run agents from anywhere.** Each configured agent is exposed as a tool through a built-in MCP server. Any assistant that supports MCP (such as Claude Code) can discover and delegate tasks to your agents without any manual wiring.

**Universal skills library.** A set of reusable skills (e.g. web research, prompt engineering, code review) can be referenced by any agent and compiled appropriately for the target platform — installed as modular skill files where supported, or inlined into the system prompt where not.

**Flexible runner backends.** Agents can be backed by different execution runtimes — agentic CLI tools, local model runners, or plain shell scripts — all configured declaratively and invoked consistently.

## Current status

This project is under active redesign. See the [open issues](https://github.com/warpcode/cloakai/issues) for the planned architecture and roadmap.
