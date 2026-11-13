# Linking your Claude Code to the team

For Ed, Bartek, Seb and Ayman. If you use Codex, see `join-codex.md` instead.

You need Python 3.11+ (`python --version`) and Claude Code (`npm install -g @anthropic-ai/claude-code`).

1. **Join the Linear team.** Ask Ed for an invite to the Junction 2026 team (key `JUN`).
2. **Make a Linear API key.** linear.app → your avatar → Settings → Security & access → Personal
   API keys → New key. Call it `claude` and copy the `lin_api_...` value. Don't put it in git or in
   Linear comments.
3. **Open the repo in Claude Code.** In PowerShell, in the repo:
   ```
   git checkout main; git pull
   claude
   ```
4. **Connect Linear (optional, for browsing issues from Claude).** In Claude, type `/mcp`, choose
   claude.ai Linear (or `linear`), click Authenticate and approve in the browser. If it isn't
   listed, quit Claude, run `claude mcp add --transport http linear https://mcp.linear.app/mcp`,
   then do `/mcp` again.
5. **Paste the bootstrap prompt.** Copy the block under the `---` in `coord/BOOTSTRAP.md` into
   Claude. Replace NAME with `ed`, `bartek`, `seb` or `ayman`, and TOOL with `claude`. Give Claude
   your API key when it asks. It saves the key in `.coord/linear.env`, which git ignores, and
   checks that it's ignored.

Once you're set up, your Claude says hello on the "Agent roll call" issue and keeps a background
watch running. When the watch wakes it with Linear updates, it acts on them. It then picks your top
`agent:<name>` issue and proposes a plan to you.

Need something from another area? File an issue with their label:

- ML, models, data or research questions (datasets, licences, docs): `agent:seb` (add `research`
  for questions)
- Backend, APIs, storage or deployment: `agent:bartek`
- Arduino, sensors or firmware: `agent:ayman`
- Anything else: `agent:ed`

The rules are in `coord/PROTOCOL.md`.
