# Coordination protocol

Four humans, four coding agents, one Linear team. Linear is the message bus: agents talk to each
other through issues and comments, using `coord/linear_bus.py` (stdlib Python, no install).

## Who does what

| Human  | Agent name | Owns                                                          | Label          |
|--------|------------|---------------------------------------------------------------|----------------|
| Ed     | `ed`       | General purpose: glue, frontend, demo, pitch, anything unowned | `agent:ed`     |
| Bartek | `bartek`   | Backend: APIs, data storage, deployment, integration           | `agent:bartek` |
| Seb    | `seb`      | ML in general: models, training, data, evaluation, research    | `agent:seb`    |
| Ayman  | `ayman`    | Arduino: firmware, sensors, wiring, device-to-backend link     | `agent:ayman`  |

If work doesn't clearly belong to someone, it goes to `agent:ed`. Agents work only inside their
owner's area. When something crosses a boundary (for example, the payload the Arduino sends to the
backend), whoever needs it files an issue for the owner and the two agents agree on the interface
in its comments.

## Markers

Linear credits every post to the human's account, so each agent starts every comment with a
marker: `[<agent>-<tool>]`, for example `[ed-claude]`, `[bartek-codex]`. The script adds it. An
agent never posts as someone else. `watch` and `check` skip the agent's own posts.

## Labels

| Label           | Meaning                                                        |
|-----------------|----------------------------------------------------------------|
| `agent:<name>`  | This agent should pick it up. One per issue.                   |
| `research`      | A question to answer in comments (datasets, licences, docs). Usually also `agent:seb`. |
| `human-needed`  | Blocked on a human decision or physical action. Mention them.  |
| `blocked`       | Waiting on another issue. Link it.                             |
| `handoff`       | Finished work another agent now needs to pick up.              |

Run `python coord/linear_bus.py setup` once to create them.

## Mentions

Write `@ed`, `@bartek`, `@seb`, `@ayman` or `@all` in a comment to flag it `>>> FOR YOU` in
that agent's feed. An issue labelled `agent:<name>` is flagged the same way.

## Agent rules

1. **Listen.** Keep a watch running (Claude Code), or run `check` before and after every task
   (Codex). Answer anything flagged `>>> FOR YOU`.
2. **Ask your human before starting.** Pick your top open `agent:<you>` issue, propose a plan, and
   wait for a yes. Move the issue to In Progress when you start.
3. **Stay in your lane.** Don't edit another owner's area. File an issue for them instead.
4. **Report on the issue.** When you finish, say what changed (files, branch or PR, how to test)
   and move the issue to Done. If someone else needs it next, add `handoff` and mention them.
5. **Keep comments short.** Say what you need, from whom, and by when. No status chatter.
6. **Never post secrets.** API keys live in `.coord/linear.env`, which git ignores. Never put them
   in comments, issues or commits.
7. **Git.** Branch per issue (`jun-12-short-name`) and open a PR to `main`. Don't push to
   another person's branch.

## Commands

```
python coord/linear_bus.py whoami                     # check the key and team
python coord/linear_bus.py inbox                      # your open agent:<you> issues
python coord/linear_bus.py check                      # poll once, print anything new
python coord/linear_bus.py watch                      # block until something new, print, exit
python coord/linear_bus.py comment JUN-12 "text"
python coord/linear_bus.py create "title" --body "..." --label agent:seb --label research
```

Settings live in `.coord/linear.env` (see `BOOTSTRAP.md`). The Linear team key defaults to `JUN`.
Set `COORD_TEAM` there if the team has a different key.
