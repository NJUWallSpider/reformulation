"""Bounded rendering over relational structure data."""
from mipframe.options import options
from mipframe.frame.entity import _text
def canonical_pattern_index(signature):
    """Choose a human-readable zero-equality direction using structure alone."""
    patterns = signature.patterns
    if signature.kind != 'row' or signature.base != ('=', 'zero') or len(patterns) < 2:
        return 0
    def rank(pattern):
        groups = total = 0
        for coefficient, _, count in pattern:
            if coefficient > 0:
                groups += 1
                total += count.min if hasattr(count, 'mode') else count
        return groups, total
    return min(range(len(patterns)), key=lambda i: rank(patterns[i]))

def structure_text(signature):
    signature.result._store.guard()
    if not signature.depth:
        return '(base role only)'
    pattern = signature.patterns[canonical_pattern_index(signature)]
    label = 'V' if signature.kind == 'row' else 'Rstate'
    terms = []
    limit = min(options.display.max_cols, 12)
    signs = (1, -1) if signature.kind == 'row' and signature.base[0] == '=' else (-1, 1)
    for sign in signs:
        for coefficient, color, count in pattern:
            if (coefficient > 0) != (sign > 0):
                continue
            if len(terms) == limit:
                return ' | '.join(terms) + f' | ... ({len(pattern)} signed groups)'
            weight = '' if signature.mode == 'template' or abs(coefficient) == 1 else f'{_text(abs(coefficient))}·'
            if hasattr(count, 'mode'):
                # A varying observed range is parameter data, not a formula
                # multiplier. Its histogram/range lives in numeric relations.
                suffix = f' ×n{count.mode}' if count.min != count.max else f' ×{count.min}'
            else:
                suffix = f' ×{count}'
            terms.append(f'{"+" if sign > 0 else "-"}{weight}{label}{color}{suffix}')
    return ' | '.join(terms) or '(no terms)'

def validate_transition(start, stop, kind):
    if type(start) is not int or type(stop) is not int or start < 0 or stop <= start:
        raise ValueError('refinement requires integer depths with 0 <= start < stop')
    if kind not in ('row', 'var'):
        raise ValueError("kind must be 'row' or 'var'")


def inspect_families(provider, frame, keys, *, verbose=False, samples=3,
                     sample_formula='auto', max_terms=12, max_items=15, max_splits=5):
    from mipframe.frame.executor import Context
    from mipframe.frame.expr import col
    lines = [repr(frame.head(max_items))]
    total = frame.count()
    if total > max_items: lines.append(f'{total-max_items} families omitted')
    selected = provider.directory().where(col('family_key').isin(
        frame.select(family_key=col(keys['family_key'])).distinct()))
    population = selected.column('count').sum() or 0
    largest = selected.sort_by('count', descending=True).head(5)
    largest_count = max(largest.column('count'), default=0)
    top5 = largest.column('count').sum() or 0
    singletons = selected.where(count=1).count()
    lines.append(f'Concentration: largest {largest_count}/{population}; top-5 coverage {top5}/{population}; singleton families {singletons}')
    if provider.result is not None:
        lines.append('Depth overview\n'+repr(frame.related('levels')))
        if provider.depth:
            from mipframe.analysis import _transitions, _options
            delta = _transitions(provider.owner, provider.depth-1, provider.depth,
                kind=provider.kind, entities=getattr(selected, provider.kind+'s'),
                method=provider.method, **_options(provider.config))
            splits = delta.where(unchanged=False)
            lines.append('Refinement (selected members)\n'+repr(splits.head(max_splits)))
            omitted = splits.count()-max_splits
            if omitted>0: lines.append(f'{omitted} transitions omitted')
            unchanged = delta.where(unchanged=True)
            lines.append(f'Unchanged families: {unchanged.count()}')
    ctx=Context((provider.owner,),None)
    try:
        for e in provider.selected(frame.head(max_items),keys,ctx):
            lines.append(f"{e['id']} at depth {provider.depth}: {e['count']} selected / {e['total_count']} total")
            if provider.result is not None:
                single=provider.directory().where(id=e['id'])
                parameters=single.related('parameters').where(
                    col('quantity').isin(('value','coefficient','multiplicity')),
                    basis='canonical',orientation=canonical_pattern_index(provider.signature(e)))
                lines.append('Numerical basis: canonical\n'+repr(parameters))
            members=provider.members(e).head(min(samples,options.display.max_rows,5))
            if samples:
                lines.append('Member samples:')
                for item in members:
                    if provider.kind=='row' and (sample_formula is True or sample_formula=='auto' and item.nnz<=max_terms):
                        lines.append(f'{item.name}: {item.formula(order="index",max_terms=max_terms)}')
                    else: lines.append(repr(item))
            if verbose:
                single=provider.directory().where(id=e['id'])
                lines.append('Diagnostics: all orientations\n'+repr(single.related('pattern')))
                if provider.result is not None:
                    lines.append(repr(single.related('sources')))
                    lines.append(repr(single.related('histogram')))
    finally: ctx.close()
    return '\n'.join(lines)
