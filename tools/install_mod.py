#!/usr/bin/env python3
"""Generate local GUI overrides from a licensed CK3 installation; never ship vanilla files."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GAME = ROOT.parent / 'game'
DEFAULT_MODS = Path.home() / '.local/share/Paradox Interactive/Crusader Kings III/mod'


def concat(*parts):
    if len(parts) == 1:
        return parts[0]
    return f'Concatenate({parts[0]}, {concat(*parts[1:])})'


def callback(expression, indent='\t\t\t', key='on_start'):
    return f'{indent}{key} = "[{expression}]"\n'


FRAME_FIELDS = {
    'event': ['key', 'title', 'body', 'opening', 'signature', 'count'],
    'option': ['key', 'title', 'index', 'text'],
    'choice': ['key', 'title', 'text'],
    'heartbeat': [], 'session_loaded': [],
}


def log_frame(kind, fields, indent='\t\t\t', key='on_start'):
    # Pass text as typed data in the very same call that exports it, rather than
    # relying on later console evaluation of mutable global UI variables.
    scope = 'GuiScope.SetRoot(GetPlayer.MakeScope)'
    for name, value in fields.items():
        scope += f".AddScope('ck3st_{name}', MakeScopeFlag({value}))"
    return callback(f"GetScriptedGui('ck3st_export_{kind}').Execute({scope}.End)", indent, key)


def scripted_guis():
    return ''.join(f'ck3st_export_{kind} = {{\n scope = character\n'
                   f' saved_scopes = {{ {" ".join("ck3st_"+field for field in fields)} }}\n'
                   f' effect = {{ info_log = ck3st_export_{kind} }}\n}}\n'
                   for kind, fields in FRAME_FIELDS.items())


EVENT_KEY = concat('EventWindowData.GetDebugInfo', "'|'", 'EventWindowData.GetTitle')
SIGNATURE = concat(EVENT_KEY, "'|'", 'EventWindowData.GetDescription')


def event_template():
    fields = {'key': EVENT_KEY, 'title': 'EventWindowData.GetTitle'}
    output = 'template ck3st_event_capture {\n\twidget = {\n\t\tname = "ck3st_event_export"\n\t\tsize = { 1 1 }\n\t\talwaystransparent = yes\n'
    output += '\t\tstate = {\n\t\t\tname = ck3st_export_header\n\t\t\ttrigger_on_create = yes\n'
    seen = concat("'ck3st_seen_event_'", EVENT_KEY)
    output += f'\t\t\ttrigger_when = "[Not(GetVariableSystem.HasValue({seen}, {SIGNATURE}))]"\n'
    output += callback(f'GetVariableSystem.Set({seen}, {SIGNATURE})')
    output += log_frame('event', {**fields, 'body': 'EventWindowData.GetDescription',
                                 'opening': 'EventWindowData.GetOpening', 'signature': 'EventWindowData.GetSignature',
                                 'count': 'IntToString(GetDataModelSize(EventWindowData.GetOptions))'})
    output += '\t\t}\n\t}\n}\n'
    return output


def option_capture(indent):
    # Capture on the game's own option widgets. A hidden, clipped model does not
    # reliably instantiate every item. Sibling shortcut widgets may repeat a frame;
    # the shared cache and companion deduplicate those without dropping equal labels.
    output = indent + 'state = {\n'
    inner = indent + '\t'
    output += inner + 'name = ck3st_export_option\n'
    index = 'IntToString(PdxGuiWidget.GetIndexInDataModel)'
    seen_option = concat("'ck3st_seen_option_'", EVENT_KEY, "'|'", index)
    option_signature = concat(SIGNATURE, "'|'", 'EventOption.GetText')
    output += inner + 'trigger_on_create = yes\n'
    output += f'{inner}trigger_when = "[Not(GetVariableSystem.HasValue({seen_option}, {option_signature}))]"\n'
    output += callback(f'GetVariableSystem.Set({seen_option}, {option_signature})', inner)
    output += log_frame('option', {'key': EVENT_KEY, 'title': 'EventWindowData.GetTitle',
                                  'index': index, 'text': 'EventOption.GetText'}, inner)
    output += indent + '}\n'
    return output


def session_widget():
    result = 'widget = {\n\tname = "ck3st_session_monitor"\n\tsize = { 0 0 }\n\talwaystransparent = yes\n'
    result += '\tstate = {\n\t\tname = ck3st_world_loaded\n\t\ttrigger_on_create = yes\n\t\ttrigger_when = "[GetPlayer.IsValid]"\n'
    result += log_frame('session_loaded', {}, '\t\t') + '\t}\n'
    result += '\tstate = {\n\t\tname = ck3st_date_changed\n'
    result += '\t\ttrigger_when = "[And(GetPlayer.IsValid, Not(GetVariableSystem.HasValue(\'ck3st_last_date\', GetCurrentDate.GetString)))]"\n'
    result += callback("GetVariableSystem.Set('ck3st_last_date', GetCurrentDate.GetString)", '\t\t')
    result += log_frame('heartbeat', {}, '\t\t') + '\t}\n}\n'
    return result


def localization():
    date = '[GetCurrentDate.GetYear].[GetCurrentDate.GetMonthOfYear].[GetCurrentDate.GetDayOfMonth]'
    base = {'date': date, 'player': '[GetPlayer.GetNameNoTooltip]'}
    lines = ['l_english:']
    for kind, fields in FRAME_FIELDS.items():
        content = f'STCK3_V1_BEGIN {kind}\\n'
        for name, value in {**base, **{f: f"[SCOPE.GetFlagName('ck3st_{f}')]" for f in fields}}.items():
            content += f'STCK3_V1_FIELD {name}\\n{value}\\n'
        lines.append(f' ck3st_export_{kind}:0 "{content}STCK3_V1_END"')
    # These are script-side hooks; use their documented script scopes, not GUI data contexts.
    messages = {
        'character_changed': ('character_changed', '[ROOT.Char.GetNameNoTooltip]', ''),
        'death': ('milestone', '[GetPlayer.GetNameNoTooltip]', '[ROOT.Char.GetNameNoTooltip] died.'),
        'marriage': ('milestone', '[GetPlayer.GetNameNoTooltip]', '[ROOT.Char.GetNameNoTooltip] married [scope:spouse.GetNameNoTooltip].'),
        'divorce': ('milestone', '[GetPlayer.GetNameNoTooltip]', '[ROOT.Char.GetNameNoTooltip] divorced [scope:spouse.GetNameNoTooltip].'),
        'birth': ('milestone', '[GetPlayer.GetNameNoTooltip]', '[ROOT.Char.GetNameNoTooltip] was born.'),
        'war_started': ('milestone', '[GetPlayer.GetNameNoTooltip]', '[scope:attacker.GetNameNoTooltip] began a war against [scope:defender.GetNameNoTooltip].'),
        'war_attacker_won': ('milestone', '[GetPlayer.GetNameNoTooltip]', '[scope:attacker.GetNameNoTooltip] won the war against [scope:defender.GetNameNoTooltip].'),
        'war_defender_won': ('milestone', '[GetPlayer.GetNameNoTooltip]', '[scope:defender.GetNameNoTooltip] defeated [scope:attacker.GetNameNoTooltip].'),
        'war_white_peace': ('milestone', '[GetPlayer.GetNameNoTooltip]', 'The war between [scope:attacker.GetNameNoTooltip] and [scope:defender.GetNameNoTooltip] ended in white peace.'),
    }
    for key, (kind, player, text) in messages.items():
        fields = {'date': date, 'player': player}
        if text:
            fields['text'] = text
        content = f'STCK3_V1_BEGIN {kind}\\n' + ''.join(f'STCK3_V1_FIELD {k}\\n{v}\\n' for k, v in fields.items()) + 'STCK3_V1_END'
        content = re.sub(r'\[scope:(\w+)\.', lambda m: "[SCOPE.sC('"+m[1]+"').", content)
        lines.append(f' ck3st_hook_{key}:0 "{content}"')
    lines += [
        ' ck3st_test_decision:0 "Test SillyTavern bridge"',
        ' ck3st_test_decision_desc:0 "Open a harmless test event to check the bridge connection."',
        ' ck3st_test_decision_tooltip:0 "A messenger will arrive with two response options."',
        ' ck3st_test_decision_confirm:0 "Receive the messenger"',
        ' ck3st_test.1.t:0 "A messenger arrives"',
        ' ck3st_test.1.desc:0 "A messenger bows before you. \\"My liege, the road to your distant correspondent is ready.\\"\\n\\nYour words may now travel beyond the borders of the realm."',
        ' ck3st_test.1.a:0 "Welcome. Let us begin."',
        ' ck3st_test.1.b:0 "You may leave now."',
        ' ck3st_diagnostic:0 "STCK3_V1_BEGIN diagnostic\\nSTCK3_V1_FIELD text\\nScript-side info_log is working.\\nSTCK3_V1_END"',
    ]
    return '\n'.join(lines) + '\n'


def on_actions():
    hooks = {
        'on_player_character_change': ('character_changed', 'is_ai = no'),
        'on_death': ('death', 'OR = { is_ai = no any_close_family_member = { is_ai = no } }'),
        'on_marriage': ('marriage', 'OR = { is_ai = no scope:spouse = { is_ai = no } }'),
        'on_divorce': ('divorce', 'OR = { is_ai = no scope:spouse = { is_ai = no } }'),
        'on_birth_child': ('birth', 'OR = { scope:mother = { is_ai = no } scope:father ?= { is_ai = no } }'),
        'on_war_started': ('war_started', 'OR = { scope:attacker = { is_ai = no } scope:defender = { is_ai = no } }'),
        'on_war_won_attacker': ('war_attacker_won', 'OR = { scope:attacker = { is_ai = no } scope:defender = { is_ai = no } }'),
        'on_war_won_defender': ('war_defender_won', 'OR = { scope:attacker = { is_ai = no } scope:defender = { is_ai = no } }'),
        'on_war_white_peace': ('war_white_peace', 'OR = { scope:attacker = { is_ai = no } scope:defender = { is_ai = no } }'),
    }
    return ''.join(f'{name} = {{ on_actions = {{ ck3st_{key} }} }}\n'
                   f'ck3st_{key} = {{\n trigger = {{ {trigger} }}\n effect = {{ info_log = ck3st_hook_{key} }}\n}}\n'
                   for name, (key, trigger) in hooks.items())


def build(game, destination):
    data = game / 'game'
    if not (data / 'gui/shared/event_windows.gui').is_file():
        raise ValueError('Expected a CK3 installation root with game/gui/shared/event_windows.gui')
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        # Only replace directories previously generated by this script.
        if not (destination / 'ck3st-generated.json').is_file():
            raise ValueError(f'Refusing to overwrite an unrelated mod directory: {destination}')
        shutil.rmtree(destination)
        destination.mkdir()
    manifest = {'game_version': json.loads((game/'launcher/launcher-settings.json').read_text()).get('rawVersion'),
                'source_sha256': {}, 'capture': 'scripted GUI with explicit text scopes to info_log'}
    sources = [data/'gui/shared/event_windows.gui', *sorted((data/'gui/event_windows').glob('*.gui'))]
    selects = 0
    for source in sources:
        text = source.read_text(encoding='utf-8-sig')
        original = text
        if source.name == 'event_windows.gui':
            text, count = re.subn(r'(template Event_Window_Debug_EventTools\s*\{)',
                                 r'\1\n\tusing = ck3st_event_capture', text)
            if count != 1:
                raise ValueError('Shared event template changed; update the generator before installing')
        # Export immediately before existing selections, including keyboard shortcut templates.
        pattern = r'(?m)^(\s*)onclick = "\[EventOption.Select\]"'
        def patch_choice(match):
            indent = match[1].split('\n')[-1]
            return option_capture(indent) + log_frame('choice', {'key': EVENT_KEY, 'title': 'EventWindowData.GetTitle',
                                        'text': 'EventOption.GetText'}, indent, 'onclick') + indent + 'onclick = "[EventOption.Select]"'
        text, count = re.subn(pattern, patch_choice, text)
        selects += count
        if text != original:
            rel = source.relative_to(data)
            output = destination/rel
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(text, encoding='utf-8-sig')
            manifest['source_sha256'][str(rel)] = hashlib.sha256(source.read_bytes()).hexdigest()
    if selects < 4:
        raise ValueError('Too few option-selection hooks found; game GUI changed')
    authored = {
        'gui/00_ck3st_capture.gui': event_template(),
        'common/scripted_guis/ck3st_exports.txt': scripted_guis(),
        'gui/ck3st_session.gui': session_widget(),
        'gui/scripted_widgets/ck3st.txt': 'gui/ck3st_session.gui = ck3st_session_monitor\n',
        'common/on_action/ck3st_on_actions.txt': on_actions(),
        'common/decisions/ck3st_test.txt': '''ck3st_test_decision = {
 picture = { reference = "gfx/interface/illustrations/decisions/decision_misc.dds" }
 desc = ck3st_test_decision_desc
 selection_tooltip = ck3st_test_decision_tooltip
 confirm_text = ck3st_test_decision_confirm
 is_shown = { is_ai = no }
 is_valid = { always = yes }
 effect = { info_log = ck3st_diagnostic trigger_event = ck3st_test.1 }
 ai_check_interval = 0
 ai_will_do = { base = 0 }
}\n''',
        'events/ck3st_test_events.txt': '''namespace = ck3st_test
ck3st_test.1 = {
 type = character_event
 title = ck3st_test.1.t
 desc = ck3st_test.1.desc
 theme = diplomacy
 option = { name = ck3st_test.1.a }
 option = { name = ck3st_test.1.b }
}\n''',
        'localization/english/ck3st_l_english.yml': localization(),
        'descriptor.mod': 'name="SillyTavern CK3 Bridge"\nversion="0.1.0"\nsupported_version="1.20.*"\ntags={ "Utilities" }\n',
        'ck3-tiger.conf': 'languages = { check = "english" }\nfilter = { show_vanilla = no show_loaded_mods = no }\n',
    }
    for name, text in authored.items():
        output = destination/name
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding='utf-8-sig' if name.endswith(('.yml', '.gui', '.txt')) else 'utf-8')
    manifest['selection_hooks'] = selects
    (destination/'ck3st-generated.json').write_text(json.dumps(manifest, indent=2)+'\n')
    return manifest


def register(destination, mods):
    mods.mkdir(parents=True, exist_ok=True)
    launcher = mods/'sillytavern_ck3_bridge.mod'
    content = (destination/'descriptor.mod').read_text() + f'path="{destination.resolve()}"\n'
    if launcher.exists() and launcher.read_text() != content:
        raise ValueError(f'Refusing to overwrite a different launcher descriptor: {launcher}')
    launcher.write_text(content)
    return launcher


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game', type=Path, default=DEFAULT_GAME)
    parser.add_argument('--destination', type=Path, default=ROOT/'.runtime/mod')
    parser.add_argument('--register', action='store_true')
    parser.add_argument('--mods', type=Path, default=DEFAULT_MODS)
    args = parser.parse_args()
    result = build(args.game.resolve(), args.destination.resolve())
    print(f'Generated mod for CK3 {result["game_version"]}: {args.destination.resolve()}')
    print(f'Patched {len(result["source_sha256"])} GUI files; {result["selection_hooks"]} selection hooks.')
    if args.register:
        print(f'Launcher descriptor: {register(args.destination.resolve(), args.mods)}')
