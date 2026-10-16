# Event filtering assessment

Category checkboxes are a modest addition. The capture already has the event ID
inside `EventWindowData.GetDebugInfo`, for example `hunt.0550`,
`spymaster_task.0346`, and `hostile_scheme_discovery.3002`. There is no need for OCR,
another game window hook, or an ST server plugin.

The substantial part is classification, not the checkbox UI. CK3's window styles
(ordinary, letter, fullscreen, activity) describe presentation, not importance.
They would be weak relevance filters on their own.

## Evidence from this installation

The installed event scripts contain 576 files declaring 504 namespaces. All but
one namespace stay within one top-level event directory. For example:

| Namespace | Source directory | Useful category |
| --- | --- | --- |
| `hunt`, `feast`, `hold_court` | `events/activities/` | Activities |
| `spymaster_task` | `events/councillor_task_events/` | Councillor reports |
| `hostile_scheme_discovery` | `events/scheme_events/` | Schemes and secrets |
| `death_management` | `events/death_events/` | Family, health, and death |

These counts describe the current local installation, which includes extra
content; they are not a fixed list for every CK3 version.

## Proposed implementation

1. Extract the event ID from each capture and send it as internal metadata on
   both the event and its choice. Do not put IDs or category labels into RP text.
2. During mod generation, build a local namespace-to-category index from event
   source directories. Use explicit overrides for ambiguous namespaces and root
   event files. This avoids fragile classification by English title or prose.
3. Add checkboxes for activities, travel, councillor reports, schemes/secrets,
   family/health, court/realm, warfare, and other/unclassified. Existing lifecycle
   and milestone controls remain separate; milestones can also split into life
   and war switches using their new `milestoneType` metadata.
4. Filter before chat insertion and AI generation. A disabled category advances
   the delivery cursor and suppresses its choices too. Keep unknown events enabled
   by default, including modded events whose sources were not indexed.
5. Test filtering across queued batches, save retries, chat switches, reloads,
   and DLC/mod namespaces. Runtime spot checks should cover each mapped category.

A first coarse directory-based version is straightforward. Accurate fine-grained
relevance, such as separating routine councillor reports from critical discoveries,
needs per-ID exceptions and iteration against events the player actually receives.
An optional namespace/ID blocklist would allow immediate tuning for custom mods.

Do not treat a single response option as an unimportant event: death notifications
and significant discoveries can have just one acknowledgement. Version 0.2 suppresses
the redundant choice message while retaining the original event.

Browser-side filtering reduces chat context and AI calls. It does not reduce CK3's
export logging; reducing that overhead would be a separate game-side change.
