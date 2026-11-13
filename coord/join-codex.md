# Linking your Codex to the team

For Ed, Bartek, Seb and Ayman. If you use Claude Code, see `join-claude.md` instead.

You need Python 3.11+ (`python --version`) and the Codex CLI (`npm install -g @openai/codex`).

1. **Join the Linear team.** Ask Ed for an invite to the Junction 2026 team (key `JUN`).
2. **Make a Linear API key.** linear.app → your avatar → Settings → Security & access → Personal
   API keys → New key. Call it `codex` and copy the `lin_api_...` value. Don't put it in git or in
   Linear comments.
3. **Open the repo in Codex, with network access.** `linear_bus.py` calls `api.linear.app`, and
   Codex's sandbox blocks network by default. In PowerShell, in the repo:
   ```
   git checkout main; git pull
   codex -c sandbox_workspace_write.network_access=true
   ```
   If you start Codex without that flag, approve the network request when Codex asks to run
   `linear_bus.py`.
4. **Connect Linear (optional, for browsing issues from Codex).** Quit Codex and run:
   ```
   codex mcp add linear --url https://mcp.linear.app/mcp
   codex mcp login linear
   ```
   Approve in the browser, then start Codex again. The bus script works without this.
5. **Paste the bootstrap prompt.** Copy the block under the `---` in `coord/BOOTSTRAP.md` into
   Codex. Replace NAME with `ed`, `bartek`, `seb` or `ayman`, and TOOL with `codex`. Give Codex your
   API key when it asks. It saves the key in `.coord/linear.env`, which git ignores, and checks that
   it's ignored.

Once you're set up, your Codex says hello on the "Agent roll call" issue, picks your top
`agent:<name>` issue and proposes a plan to you.

**Codex can't be woken by Linear in the background.** It runs
`python coord/linear_bus.py check` before and after each task. If you've been away, say "check
Linear" to catch up.

Need something from another area? File an issue with their label:

- ML, models, data or research questions (datasets, licences, docs): `agent:seb` (add `research`
  for questions)
- Backend, APIs, storage or deployment: `agent:bartek`
- Arduino, sensors or firmware: `agent:ayman`
- Anything else: `agent:ed`

The rules are in `coord/PROTOCOL.md`.
