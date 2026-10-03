"""Rendering keeps complete capsules and never cuts a code example in half."""


def card(t, full=False):
    parts = [f"{t['title']} [{t['id']}]", t['summary']]
    for symbol, signature in t.get('signatures', {}).items():
        parts.append(symbol + signature)
    for label, key in [('When to use', 'when'), ('Inputs', 'inputs'), ('Returns', 'returns'),
                       ('Establishes', 'guarantees'), ('Does not establish', 'non_guarantees'),
                       ('Failures', 'failures'), ('Resources', 'resources')]:
        if t.get(key):
            parts.append(f"{label}: {t[key]}")
    if t.get('example'):
        label = ('Tested recipe (requires the declared inputs):' if t.get('example_kind') == 'tested_recipe'
                 else 'Illustrative example (requires caller-provided inputs):')
        parts.append(label + '\n' + t['example'])
    if (full or t['id'] == 'home') and t.get('body'):
        parts.append(t['body'])
    if t.get('related'):
        parts.append('See also: ' + ', '.join(t['related']))
    if not full and not t.get('complete', not bool(t.get('body'))):
        parts.append(f"More: mf.help({t['id']!r}, detail='full')")
    return '\n\n'.join(parts)


def render(result):
    if result['detail'] == 'full' and len(result['results']) == 1 and result['results'][0]['id'] == 'skill' and not result['warnings']:
        return result['results'][0]['body']
    lines = [f"MIPFrame {result['version']} help"]
    lines.extend('Documentation mismatch: ' + w for w in result['warnings'])
    if not result['results']:
        lines.append('No matching topic. No API recommendation was generated.')
        if result.get('suggestions'):
            lines.append('Possible names: ' + ', '.join(result['suggestions']))
        lines.append('Use mf.help() for the capability map.')
    lines.extend(card(t, result['detail'] == 'full') for t in result['results'])
    return '\n\n'.join(lines)
