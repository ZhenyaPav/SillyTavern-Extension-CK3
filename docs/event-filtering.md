# Event category filters

Version 0.3 adds 15 checkboxes under the collapsed **Event categories** section.
All start enabled for compatibility. **Select all** enables all 15; **Clear all**
disables all 15; **Story-focused** disables the three recurring-message categories.
Category preferences are saved in ST extension settings alongside message wording.

| Category | Story-focused |
|---|---|
| Family & relationships | On |
| Health, death & stress | On |
| Routine councillor & secret reports | Off |
| Homage & routine vassal requests | Off |
| Court & realm politics | On |
| War & combat | On |
| Scheme developments | Off |
| Scheme finales & exposure | On |
| Religion | On |
| Culture | On |
| Lifestyle & education | On |
| Activities & celebrations | On |
| Travel & adventuring | On |
| Artifacts, books & legends | On |
| Story chains & other events | On |

## Classification

`event-categories.json` is the shared definition and rule source. The installer
creates `.runtime/mod/ck3st-event-categories.json` from installed event scripts.
Quoted/commented examples are ignored. Higher event override priorities win;
equal-priority conflicting classifications fall back to Other. Regenerate after
game updates or rule changes.

The companion extracts an event ID from the debug key already exported by the
existing GUI hook. Event-ID rules take precedence over namespace rules, then
source-directory rules. Namespace rules still work without a generated index;
the health response reports category support and whether that index was loaded.
The event ID, category and hashed parent key remain internal metadata and are
copied to the selected response. No additional game capture hooks are required.

Important exceptions:

- Homage and loyalty pledges use the routine-vassal category.
- Governor confirmations `ep3_decisions_event.2001/2002/2010/2011/2080/2090` use it too.
- Routine petition acknowledgements, pardon and contract correspondence have
  explicit IDs. Title disputes, independence and war-related refusal letters
  remain political events. Being a letter does not determine the category.
- Spymaster task reports include discovered secrets and unsuccessful searches.
  Known secret leaks and interrupted-scheme events have exceptions preserving
  them in Scheme finales & exposure.
- `scheme_critical_moments.0001/.0002/.0011` are developments or reminders;
  its result events in the 1001–1999 range use Scheme finales & exposure.
- Scheme outcome namespaces and hostile-scheme exposure use Scheme finales & exposure.
  Ongoing events and agents use Scheme developments.

The rules are a first classification, not an engine-provided importance measure.
Source buckets mix subjects; further exact-ID exceptions may be needed. A dramatic
encounter inside an ongoing scheme currently inherits the development category
unless explicitly overridden. Event prose is never scanned for English keywords
or sent to an AI classifier.

Unknown IDs and legacy records without metadata use Other. Separate Workshop mod
directories are not indexed automatically. Rules for recognized namespaces still
apply to those events; otherwise they stay in Other. Other starts enabled in
Select all and Story-focused, and can be changed individually.

## Delivery semantics

Filters apply to pending records before saving or generating. A filtered record
advances the cursor; changing a checkbox cannot resurrect skipped traffic.
Existing chat history is not edited. Failed saves leave the cursor and parent
decision unchanged for retry.

Parent decisions are stored with the browser-local chat binding. A response is
imported only if its parent was allowed and its category is still enabled.
Enabling a category after skipping its parent cannot create an orphan choice.
Binding/unbinding resets this history; it is bounded to 512 parent keys. An older
unmatched choice is safely skipped. Legacy choices without parent metadata retain
category-only behavior.

Life/war milestones use their family, health or war category and their existing
master switch. Opened/closed games, loaded saves and character changes use the
separate session switch and are unaffected by category presets.

Choosing an event's only response still creates no additional choice message.
The event itself is subject to its category filter. Browser-side filtering reduces
chat context and AI calls; it does not reduce CK3's export logging.

## Updating and validation

Update/reload the ST extension, regenerate the index with
`python3 tools/install_mod.py`, and restart the companion. No game GUI/script hooks
change for this feature. If CK3 is running, fully exiting before stopping the
companion preserves launch/close tracking; the next Steam launch starts the updated
service. Pairing, chat binding and the outbox survive.

Unit tests cover classification exceptions, source parsing, override priorities,
zero-padded IDs, unknown events, presets and parent/choice behavior. The isolated
Firefox test covers the controls, skipped cursor advancement, retained finales and
political letters, no AI replies for filtered batches, and no replay or orphan
choices after re-enabling a category. These are automated/browser checks; accuracy
across live CK3 content needs playtesting.
