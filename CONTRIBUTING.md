# Contributing

Thanks for helping improve Worx Vision Cloud PLUS.

## Local Checks

Before opening a pull request:

1. Make sure Home Assistant starts with the integration installed.
2. Add or update translations when entity names change.
3. Do not commit `__pycache__`, dashboard backups, raw API dumps or Home Assistant storage files.
4. Remove private data from logs and screenshots.

## Coding Style

- Keep entities stable once published.
- Prefer small, readable helpers over large ad-hoc parsing blocks.
- Treat private Worx API fields defensively because they can disappear or change shape.
- Do not expose large raw payloads as entity attributes by default.
