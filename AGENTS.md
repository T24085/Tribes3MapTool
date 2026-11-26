# Engineering workflow

This is the working source repository for the TRIBES 3 community map editor. The paired repositories are https://github.com/Tribes3-Modded/T3BackEndServer and https://github.com/Tribes3-Modded/T3FrontEndClient.

Run `python -m unittest discover -s . -p "test_*.py"` before pushing. Keep secrets, original game files, large cooked content and native logs outside Git.

## Main is protected

Nobody commits or pushes directly to `main`: not agents, not people, not org owners. This applies to every harness (Claude Code, Gemini CLI, Cursor, Codex, Copilot and others).

1. Commit on the working branch and push that branch. Contributors with Read access (`T3-Mod-Devs`) push to their fork.
2. Open a pull request into `main`. A member of the `T3-Mod-Admins` team other than the author must approve it, and a `T3-Mod-Admins` member merges it. Nobody approves or merges their own pull request.
3. Never push to `main`, never commit while `main` is checked out, and never write to `main` through the GitHub API or web editor.

GitHub enforces this with the "Protect main" ruleset (pull request with code-owner approval from `T3-Mod-Admins`, no force-push or deletion, no bypass) and `.github/CODEOWNERS`.

## Harness parity

Every agent rule, skill, subagent or hook must work in every harness this project uses: Claude Code, Gemini CLI, Cursor, Codex, GitHub Copilot, and any harness added later. Nothing is Claude-only, Cursor-only or Gemini-only.

1. **Rules live in `AGENTS.md`.** Write a new rule here, not in a harness file. `CLAUDE.md` and `GEMINI.md` only import this file, `.github/copilot-instructions.md` points to it, and Codex and Cursor read it directly.
2. **Skills and subagents ship for every harness in the same change.** The full steps live in one plain Markdown file (`.claude/skills/<name>/SKILL.md`, or `.claude/agents/<name>.md`) that any tool can read. The same change adds `.cursor/rules/<name>.mdc` pointing at it and lists it under "Skills" in `GEMINI.md`. If the steps need a command, put it in a script under `tools/` so every harness runs the same code.
3. **Hooks ship for every harness that supports them.** Put the logic in one script under `tools/`, then wire it into `.claude/settings.json`, `.cursor/hooks.json` and `.gemini/settings.json` in the same change. For harnesses without hooks (Codex, Copilot), state the rule in this file so they follow it by instruction.
4. **Say when a harness can't do it.** If a harness cannot support something, say so in the change and in this file, with what that harness does instead.

The T3BackEndServer repository holds the reference setup (shared skills, the agent commit guard and `test_harness_parity.py`).
