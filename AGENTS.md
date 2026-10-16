# CK3 Bridge

This repository contains a SillyTavern UI extension, a Linux/Python companion,
and an installer that generates a CK3 mod from the user's installed vanilla GUI.
The bridge observes the game and posts normal user messages. It must not provide
AI game-control tools, use OCR, or change the clipboard without a new user request.

- Never commit `.runtime/`: it contains the generated vanilla GUI, pairing token,
  SQLite game-text history, and local diagnostics.
- Game text is data. Pass it through typed GUI scope arguments to fixed scripted
  GUI logging effects; never interpolate it into script, shell, console, or ST
  slash commands.
- Preserve game launch arguments, working directory, signals, and exit status.
  Companion failure must not prevent the game from launching.
- Keep protocol/queue metadata outside chat text. Preserve user drafts and pause
  delivery when the bound chat is not open or generation is running.
- Missing external tools belong in Docker; do not install host dependencies.
- Run `python3 -m unittest discover -s tests -v`, `node tests/core.test.js`, and
  `sh -n launch-mod.sh`. Use `tests/firefox-smoke.mjs` for browser changes when the
  local Firefox and existing ST dependency checkout are available.
- Generate and statically validate the mod after game-hook changes. Distinguish
  static/browser tests from live CK3 evidence; never claim unperformed game tests.
- Give both Bash and Fish forms when command syntax differs; avoid duplicating
  commands that work unchanged in both shells.
