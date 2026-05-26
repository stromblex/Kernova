## Usage

```bash
cd updater
uv sync
uv run kernova-update build
```

Output: `builds/` | Changelogs: `changelogs/`

If nothing changed since last build, it skips. Use `--force` to override.
