# SillyTavern CK3 Bridge

A one-way Crusader Kings III → SillyTavern bridge. A CK3 mod exports tagged log
records, a local Python companion reads them, and a SillyTavern UI extension adds
ordinary **user messages** to a bound chat. The ST server can run on another host.
No ST server plugin, OCR, clipboard monitoring, or AI game-control tools are used.

**Version 0.3 is an experimental build.** The companion and frontend are
independently testable. Game-side capture passes event text as explicit string
scopes to scripted GUI effects that write fixed `info_log` localization records.
It uses no console commands. The scope-based handoff has been confirmed in-game
with the test event; broader event-surface coverage still needs testing.
Start with a separate single-player test save;
Ironman/multiplayer support and achievement compatibility are not established.

## First setup on the gaming PC

Requires Linux and Python 3.11 or newer. The companion has no pip dependencies.
Node is needed only for development tests. Missing external development tools
should run in Docker rather than being installed on the host.

From this repository:

```sh
python3 tools/install_mod.py --game "/mnt/ssd/SteamLibrary/steamapps/common/Crusader Kings III" --register
python3 companion/bridge.py ensure
python3 companion/bridge.py token
```

The generator creates `.runtime/mod` from **your own installed game files** and
registers `sillytavern_ck3_bridge.mod` in your local CK3 mod directory. Enable
**SillyTavern CK3 Bridge** in the launcher playset. The generated overrides are
based on CK3 **1.20.1.1** during initial development. Regenerate after game updates.
They are kept out of Git; this repository does not redistribute vanilla GUI files.

In SillyTavern, install this UI extension using:

```text
https://github.com/ZhenyaPav/SillyTavern-Extension-CK3
```

Reload ST, open the intended chat, and expand **Extensions → CK3 Bridge**:

1. Keep `http://127.0.0.1:17653` as the companion address.
2. Paste the pairing token printed by the command above.
3. Click **Test connection**, then **Bind this chat**.
4. Enable **Receive game messages**. Automatic AI replies are enabled by default
   and can be disabled independently.
5. Click **Send sample event** to check the companion → ST path before running CK3.

Pairing credentials and the binding/cursor stay in this browser's local storage,
not ST's synchronized settings. The default allowed ST origin is
`https://nova-st.pve.realm.bz`. The HTTPS site must be allowed by Firefox to contact
the local companion. A valid HTTPS certificate with a private DNS address is fine;
browser-specific loopback restrictions still apply. The companion binds only to
loopback and checks its token, request Host, and browser Origin.

## Steam launch options

Use an absolute path for the wrapper. It preserves Steam's working directory,
passes every argument unchanged, and retains the launched command's exit status.
If companion startup fails, the game still launches and the wrapper reports the problem.
For this workspace, including your existing wrapper:

```sh
sh /home/zhenyapav/Projects/ck3-mods/SillyTavern-Extension-CK3/launch-mod.sh bash ./cream.sh %command%
```

Without that wrapper:

```sh
sh /home/zhenyapav/Projects/ck3-mods/SillyTavern-Extension-CK3/launch-mod.sh %command%
```

`cream.sh` is a Bash script and does not need executable permissions when invoked
through `bash`. Passing a non-executable `./cream.sh` directly will fail with
`Permission denied`. The bridge does not change the game's directory or the
inherited `PWD`; its own path lookup runs in a subshell.

These commands work unchanged from Bash and Fish; Steam itself substitutes
`%command%`. The bridge starts its companion automatically, launches the supplied
command, and monitors surviving CK3 processes in that launch's process group if
an intermediate wrapper exits early. Wrappers that daemonize CK3 into a different
session are not supported yet. Signals are forwarded to the launched process group.

After exit, the companion remains available for two minutes to deliver the final
message. Its SQLite outbox survives shutdown. Keep ST open to receive it, or restart
the companion with `ensure` to receive pending messages later. The wrapper reuses
an existing companion rather than starting duplicate listeners.

## What appears in chat

```text
A messenger arrives

*19.03.1081*

A messenger bows before you...

*Response options:*
Welcome. Let us begin.
You may leave now.
```

```text
*Alex chose: Welcome. Let us begin.*
*19.03.1081*
```

`Alex` comes from the active ST persona (`{{user}}`), not the CK3 character name.
Launch/close and loaded-character messages are short prose. Main-menu launch has
no game date or character yet; those values are reported only after the mod exports
them. Closure uses the last observed in-game date. Internal framing, event IDs,
sequence numbers, and deduplication metadata never enter message text.

**Extensions → CK3 Bridge → Message wording** lets you edit every bridge-authored
chat line: opened/closed/loaded/changed-character messages, choice narration, date
lines, the response-options heading, and individual birth/death/marriage/divorce/war
messages. Defaults wrap these lines in asterisks. Event titles, prose, openings,
signatures, and option labels come directly from CK3 and are kept as game text.

Templates support `{{user}}` (ST persona), `{{player}}` (CK3 player), `{{date}}`
(DD.MM.YYYY), `{{subject}}` / `{{other}}` (people in a milestone), and `{{text}}`
(selected option or legacy milestone text). Substitution runs once; template-like
text inside an event or character name is never evaluated. Empty narration hides
that message and does not trigger an AI reply. An empty date or options heading
hides only that line. **Reset message wording** restores the defaults. Wording
is saved in ST extension settings, separately from browser-local pairing details.

Choosing the only option in an event sends no separate choice message and starts
no additional AI reply. The event itself, including its option, is still sent.
Choices from events with multiple options remain visible.

The extension pauses while another chat is open, while an AI reply is generating,
or while your input has an unsent draft/attachment. It does not replace your draft,
evaluate incoming slash commands, switch chats, or start a new chat. Pending game
messages are imported in order. When several collect while busy, they share one
AI generation after delivery. Failed saves leave the delivery cursor unchanged.
Message metadata prevents a saved event from being imported again after reload.
Switching tabs uses a Web Lock and a leader lease to avoid duplicate delivery.

The bridge adds no recurring prompt instructions and does not automatically
summarize, delete, or prune your chat. ST's normal context selection still applies.
Use the category filters, lifecycle/milestone switches, or disable automatic replies
to control volume.
Binding starts from the current end of the companion outbox; bind **before** launch
or the next events you want to receive. Unbinding disables receiving.

## Game coverage and first test

The shared vanilla event template exports title, description, letter opening and
signature. The actual option buttons export each response from the displayed
event's model, including disabled responses. These hooks cover regular,
letter/anonymous-letter, fullscreen, duel, scheme, and activity event surfaces.
Option callbacks emit a short choice record before retaining the original game
selection. These hooks need live verification for each surface; custom UI mods
that replace the same templates may require a compatibility patch.

Additional additive `on_action` hooks cover character changes, player/close-family
deaths, marriages/divorces involving the player, the player's children, and wars
where the player is the principal attacker/defender (start, victory, white peace).
This first build does not claim to capture every toast, alert, battle result,
activity-log entry, hidden AI event, or custom total-conversion interface.

**Extensions → CK3 Bridge → Event categories** contains 15 checkboxes in a collapsed
section, with **Select all**, **Clear all**, and **Story-focused** presets. Existing
settings initially receive all categories, preserving previous delivery. Story-focused
turns off routine councillor/secret reports, homage/routine vassal requests, and scheme
developments. It keeps finales, plot exposure, and the other story categories enabled.

Filters apply when pending records are imported; skipped records advance the cursor,
stay out of chat, and do not trigger AI generation. Re-enabling a category does not
replay skipped events or import their orphaned choices. Already saved chat messages
are unchanged. Life/war milestones follow their subject category as well as their
master switch; game/session messages use the separate session switch.

The generator indexes installed event IDs using source files and explicit exceptions.
Unknown or older uncategorized records use **Story chains & other events**, enabled
by default. Categories and IDs remain internal metadata. See
[docs/event-filtering.md](docs/event-filtering.md) for the category list, exceptions,
and remaining classification limits.

To test in normal mode:

1. Enable the mod, bind ST, then launch through the Steam wrapper and load a test save.
2. Take the **Test SillyTavern bridge** decision. It logs a script-side diagnostic
   and opens a harmless two-option event.
3. Check that ST receives the event with both options, then your selected response.
4. Exercise a letter, activity event, fullscreen event, character succession, and
   save reload during ordinary play. Close CK3 and check the final message.

If the sample button works but game events do not:

```sh
python3 companion/bridge.py status
```

`frameCounts.diagnostic` confirms the mod and script-to-log path worked. If this
increments but `event`/`option` do not, the GUI-to-log route needs adjustment rather
than any ST server plugin. Inspect fresh CK3 `error.log` entries around
`ck3st`, scripted GUI, or localization errors. A pending incomplete
snapshot is shown in the extension status instead of being sent without options.

After changing the game hooks, regenerate the mod, fully exit CK3, stop the old
companion, and launch CK3 again through the Steam wrapper. Reloading ST or loading
a save alone does not load new scripted GUI definitions. The pairing token and
chat binding survive a companion restart.

```sh
python3 tools/install_mod.py
python3 companion/bridge.py stop
```

Local diagnostics are `.runtime/bridge/companion.log`, `.runtime/mod/ck3st-generated.json`
(source hashes and game version), and your normal CK3 logs. `.runtime` also holds
the local pairing token and game-text outbox; do not publish it.

To stop the companion manually:

```sh
python3 companion/bridge.py stop
```

## Development

```sh
python3 -m unittest discover -s tests -v
node tests/core.test.js
sh -n launch-mod.sh
```

An additional browser integration test uses an isolated installed Firefox profile,
its built-in WebDriver BiDi endpoint, an intercepted/mock HTTPS ST page, and a real
temporary companion. It does not use your real ST data or browser profile:

```sh
node tests/firefox-smoke.mjs /home/zhenyapav/Projects/SillyTavern
```

The argument provides the already-installed `ws` library. Runtime operation of the
extension and companion does not depend on that checkout. Tests must not be confused
with evidence that the running CK3 engine has accepted the hooks.

License: AGPL-3.0. See `LICENSE`.
