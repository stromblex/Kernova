## Build

Install the updater once, then build the loader profiles:

```bash
cd updater
uv sync
uv run kernova-update fabric
uv run kernova-update neoforge
```

Use `--force` to rebuild when the resolved mod versions did not change.

Output:

- `builds/` - generated Minecraft instances with downloaded mods.
- `changelogs/` - generated release changelogs.
- `updater/state/version_history.json` - local build/version history.

