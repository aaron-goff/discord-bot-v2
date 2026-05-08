# Discord Bot v2 — CLAUDE.md

## Project Overview

MLB/MiLB Discord bot using Discord.py slash commands with autocomplete. Data comes from two sources:
- **MLB Stats API** (`statsapi.mlb.com/api/v1`) — schedules, rosters, game logs, box scores, standings
- **Baseball Savant** (`baseballsavant.mlb.com`) — Statcast/percentile data

## Structure

- `main.py` — Bot entry point, loads cogs, syncs slash commands
- `cogs/mlb.py` — All slash command definitions (`/mlb`, `/milb`, `/savant`)
- `core/mlb_client.py` — API client (all HTTP calls live here)

## Development Workflow

After any code change, restart the dev service:

```bash
systemctl --user restart natsbot-dev.service
```

Check status / tail logs:

```bash
systemctl --user status natsbot-dev.service
journalctl --user -u natsbot-dev.service -f
```

**Never** use `pkill -f "python3 main.py"` — it risks leaving orphan processes and could interfere with the production service.

### Services
- **Dev**: `natsbot-dev.service` (user service, runs from `/home/eric/git/discord-bot-v2`, uses `.env` in that dir)
- **Production**: `natsbot.service` (system service, managed by GitHub Actions + systemd, do not touch)

## Git Workflow

- Active branch: `development`
- Deploy target: `main` (GitHub Actions auto-deploys on push)
- Push `development` for in-progress work; PR/merge to `main` to deploy
- After merging a PR on GitHub (use **Merge commit**), sync development back:
  ```bash
  git fetch origin && git merge origin/main && git push origin development
  ```

## Environment

Requires `.env` with `DISCORD_TOKEN=...`.

## Key Patterns

- Autocomplete uses Savant search first, falls back to MLB Stats API for fresh callups not yet indexed by Savant
- Historical game lookups use `gameLog` stat type to find the correct team/gamePk for a given date (avoids `currentTeam` being wrong for traded players)
- Discord output is monospace code blocks with fixed-width alignment

## Discord Commands
If the user sends "!compact", run /compact immediately and confirm.
If the user sends "!clear", run /clear immediately and confirm.
If the user sends "!cost", run /cost and report the result back via Discord.
