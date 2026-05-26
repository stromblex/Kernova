## Usage

```bash
cd updater
uv sync
uv run kernova-update build
```

You'll be asked for build name and MC version interactively.

Non-interactive:
```bash
uv run kernova-update build --name Kernova --mc 1.21.1 --yes
```

Options:
- `--name` — build name (asked if omitted)
- `--mc` — Minecraft version (asked if omitted)
- `--version` — override auto version
- `--yes` / `-y` — skip confirmations
- `--force` / `-f` — rebuild even if nothing changed

Output: `builds/` | Changelogs: `changelogs/`

If nothing changed since last build, it skips. Use `--force` to override.

## Version tags

Stability suffix based on % of modlist resolved:
- `alpha` — <60%
- `beta` — 60-89%
- `release` — 90%+