"""Presentation layers over immutable structural colors and numeric observations.

Overview paths read only colors/metadata. Parameter summaries and full numerical
histograms are explicit drill-downs, never inputs to refinement.
"""

from collections.abc import Mapping
from collections.abc import Sequence
from fractions import Fraction
from itertools import islice
import sqlite3

from mipframe.frame.entity import _Frame
from mipframe.frame.entity import _table
from mipframe.frame.entity import _text
from mipframe.options import options


def degree_text(value):
    return f'{value[0]}..{value[1]}' if isinstance(value, tuple) else str(value)


def _validate_limits(**limits):
    for name, value in limits.items():
        if type(value) is not int or value < 1:
            raise ValueError(f'{name} must be a positive integer')


def validate_summary_limits(max_families, max_splits):
    _validate_limits(max_families=max_families, max_splits=max_splits)


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
                # multiplier. Its histogram/range lives in .params.
                suffix = f' ×n{count.mode}' if count.min != count.max else f' ×{count.min}'
            else:
                suffix = f' ×{count}'
            terms.append(f'{"+" if sign > 0 else "-"}{weight}{label}{color}{suffix}')
    return ' | '.join(terms) or '(no terms)'


class NumericParameter:
    """Summary by default; the full exact histogram is an explicit query."""
    def __init__(self, distribution):
        self.distribution = distribution

    def __getattr__(self, name):
        if name in ('constant', 'varying', 'distinct', 'min', 'max', 'count'):
            return getattr(self.distribution, name)
        raise AttributeError(name)

    def value_counts(self):
        return self.distribution

    def __repr__(self):
        size = self.distribution.distinct
        if not size:
            return 'empty'
        if size == 1:
            return f'constant({_text(self.distribution.min)})'
        return f'varying[{_text(self.distribution.min)},{_text(self.distribution.max)}], distinct={size}'


class RoleParameter(NumericParameter):
    def __init__(self, role):
        self.role = role
        super().__init__(role.coefficients)

    @property
    def multiplicity(self):
        return NumericParameter(self.role.multiplicity)

    def coefficient_multiplicity(self, value):
        return NumericParameter(self.role.coefficient_multiplicity(value))


class FamilyParameters(Mapping):
    """Signed role keys avoid conflating positive/negative terms of one color."""
    def __init__(self, family):
        self.family = family

    @property
    def profile(self):
        index = canonical_pattern_index(self.family.signature)
        return self.family.attributes.orientations[index]

    def _key(self, role):
        return f'{"+" if role.sign > 0 else "-"}{role.neighbor_kind}{role.neighbor}'

    def __iter__(self):
        for role in self.profile.roles:
            yield self._key(role)

    def __len__(self):
        return len(self.profile.roles)

    def __getitem__(self, key):
        if not isinstance(key, str):
            raise TypeError('Use a role label, such as V1 or -V1')
        if '@' in key:
            raise KeyError(key)
        match = None
        for role in self.profile.roles:
            label = self._key(role)
            if key == label:
                return RoleParameter(role)
            if key == label[1:]:
                if match is not None:
                    raise ValueError(f'Ambiguous role {key!r}; use +{key} or -{key}')
                match = role
        if match is None:
            raise KeyError(key)
        return RoleParameter(match)

    @property
    def rhs(self):
        return NumericParameter(self.profile.rhs)

    @property
    def lhs(self):
        return NumericParameter(self.profile.lhs)

    def value_counts(self):
        return CoefficientCounts(self)

    def __repr__(self):
        family = self.family
        family._store.owner.model
        lines = [f'<Parameters {family.id}: {family.count:,} members; canonical magnitude; roles @ depth={max(0, family.signature.depth-1)}>']
        limit = min(options.display.max_rows, 12)
        for role in islice(self.profile.roles, limit):
            value = RoleParameter(role)
            lines.append(f'  {self._key(role)}: coefficient={value!r}; multiplicity={value.multiplicity!r}')
        if len(self) > limit:
            lines.append(f'  ... ({len(self)} roles; access signed roles individually)')
        lines.extend([f'  lhs: {self.lhs!r}', f'  rhs: {self.rhs!r}'])
        return '\n'.join(lines)

    def _repr_pretty_(self, printer, cycle):
        printer.text(repr(self))


class CoefficientCounts(Mapping):
    def __init__(self, parameters):
        self.parameters = parameters

    def __iter__(self):
        return iter(self.parameters)

    def __len__(self):
        return len(self.parameters)

    def __getitem__(self, key):
        return self.parameters[key].value_counts()

    def __repr__(self):
        lines = [f'<Coefficient distributions {self.parameters.family.id}>']
        for key in islice(self, min(options.display.max_rows, 12)):
            lines.append(f'  {key}: {self[key]!r}')
        if len(self) > min(options.display.max_rows, 12):
            lines.append(f'  ... ({len(self)} roles)')
        return '\n'.join(lines)


class FamilyInspection:
    def __init__(self, family, *, verbose=False, samples=3, sample_formula="auto", max_terms=12):
        if type(verbose) is not bool:
            raise TypeError('verbose must be a boolean')
        _Frame._size(samples)
        if sample_formula != "auto" and type(sample_formula) is not bool:
            raise ValueError("sample_formula must be 'auto', True, or False")
        _Frame._size(max_terms)
        self.family, self.verbose, self.samples = family, verbose, samples
        self.sample_formula, self.max_terms = sample_formula, max_terms

    @property
    def parameters(self):
        return self.family.params

    @property
    def members(self):
        return self.family.rows if self.family._store.kind == 'row' else self.family.vars

    @property
    def neighbors(self):
        return self.members.neighbors()

    def __repr__(self):
        f = self.family
        parameters = f._parameters
        changes, varying_count = [], 0
        for role in parameters.profile.roles:
            if role.coefficients.varying:
                varying_count += 1
                if len(changes) < 12:
                    changes.append(parameters._key(role))
        lines = [repr(f), "Numerical basis: canonical (original coefficients remain accessible on member edges)",
                 "Varying coefficient roles: " + (", ".join(changes[:12]) + (" ..." if varying_count > 12 else "") if changes else "none"),
                 repr(parameters)]
        sample = self.members.head(min(self.samples, options.display.max_rows, 5))
        if len(sample):
            lines.append('Member samples (original rows):' if f._store.kind == 'row' else 'Member samples:')
            if f._store.kind == 'var' or self.sample_formula is False:
                lines.append(repr(sample))
            else:
                metadata_rows = []
                for ident in sample.ids:
                    row = sample.loc[ident]
                    if self.sample_formula is True or row.nnz <= self.max_terms:
                        lines.append(f'  {_text(row.name)}: {row.formula(order="index", max_terms=self.max_terms)}')
                    else:
                        metadata_rows.append(ident)
                if metadata_rows:
                    lines.append(f'Metadata for rows with more than {self.max_terms} terms:\n' + repr(sample._select(tuple(metadata_rows))))
        if self.verbose:
            lines.extend(['Diagnostics (all orientations; bounded previews, complete mappings available):',
                          repr(f.signature), f.attributes.details()])
        return '\n'.join(lines)

    def _repr_pretty_(self, printer, cycle):
        printer.text(repr(self))


def validate_transition(start, stop, kind):
    if type(start) is not int or type(stop) is not int or start < 0 or stop <= start:
        raise ValueError('refinement requires integer depths with 0 <= start < stop')
    if kind not in ('row', 'var'):
        raise ValueError("kind must be 'row' or 'var'")


class RefinementDelta(Sequence):
    """Only changed families, with independent unchanged totals; no numeric scan."""
    def __init__(self, result, start, stop, *, kind='row', frame=None):
        validate_transition(start, stop, kind)
        result._check_depth(stop)
        from mipframe.ir.signatures import structural_families
        self.result, self.start, self.stop, self.kind = result, start, stop, kind
        frame = frame if frame is not None else result._store.owner.rows if kind == 'row' else result._store.owner.vars
        self.before = structural_families(result, frame, start)
        self.after = structural_families(result, frame, stop)
        self.db = sqlite3.connect('')
        try:
            self.db.execute('PRAGMA cache_size=-512')
            self.db.execute('CREATE TABLE links(parent INTEGER,child INTEGER,n INTEGER,PRIMARY KEY(parent,child))')
            old = result._store.array(result._config, kind, start)
            for position, member, count in self.after._store.db.execute('SELECT position,first_row,n FROM families ORDER BY position'):
                color = int(old[member])
                parent = self.before._store.db.execute('SELECT position FROM families WHERE color=?', (color,)).fetchone()[0]
                self.db.execute('INSERT INTO links VALUES(?,?,?)', (parent, position, count))
            self.db.commit()
            self.db.execute('PRAGMA query_only=ON')
        except BaseException:
            self.db.close()
            raise

    def __del__(self):
        if hasattr(self, 'db'):
            self.db.close()

    def _guard(self):
        self.result._store.guard()

    @property
    def before_count(self):
        return len(self.before)

    @property
    def after_count(self):
        return len(self.after)

    @property
    def unchanged(self):
        self._guard()
        count, members = self.db.execute('SELECT count(*),coalesce(sum(n),0) FROM (SELECT sum(n) AS n FROM links GROUP BY parent HAVING count(*)=1)').fetchone()
        return {'families': count, 'members': members}

    def __len__(self):
        self._guard()
        return self.db.execute('SELECT count(*) FROM (SELECT parent FROM links GROUP BY parent HAVING count(*)>1)').fetchone()[0]

    def _family(self, frame, position):
        return frame.loc[f'F{position}']

    def __iter__(self):
        self._guard()
        for parent, count in self.db.execute('SELECT parent,sum(n) FROM links GROUP BY parent HAVING count(*)>1 ORDER BY parent'):
            self._guard()
            source = self._family(self.before, parent)
            yield {'parent': source.id, 'parent_depth': self.start, 'count': count,
                   'children': DeltaChildren(self, parent)}

    def __getitem__(self, position):
        if isinstance(position, slice):
            return tuple(self[i] for i in range(*position.indices(len(self))))
        if type(position) is not int:
            raise TypeError('Use an integer position or slice')
        if position < 0:
            position += len(self)
        if not 0 <= position < len(self):
            raise IndexError(position)
        return next(islice(self, position, position+1))

    def render(self, *, max_splits=12, max_children=12, details=True):
        _validate_limits(max_children=max_children, max_splits=max_splits)
        unit = 'rows' if self.kind == 'row' else 'variables'
        lines = [f'Refinement depth {self.start} → {self.stop} ({self.result.mode})',
                 f'{self.before_count} → {self.after_count} {self.kind} families; {len(self)} split']
        limit = min(options.display.max_rows, max_splits)
        child_limit = min(options.display.max_rows, max_children)
        for split in islice(self, limit):
            lines.append(f"  {split['parent']} ({split['count']:,} {unit})")
            for child in islice(split['children'], child_limit):
                detail = f"  {child['structure']}" if details else ''
                lines.append(f"    → {child['id']}: {child['count']:,} {unit}{detail}")
            if len(split['children']) > child_limit:
                lines.append(f"    ... ({len(split['children'])-child_limit:,} additional children omitted)")
        if len(self) > limit:
            lines.append(f'  ... ({len(self)-limit:,} additional splits omitted; use .render(max_splits=...) to expand)')
        unchanged = self.unchanged
        lines.append(f"Unchanged: {unchanged['families']} families / {unchanged['members']:,} {unit}")
        return '\n'.join(lines)

    def __repr__(self):
        return self.render()

    def _repr_pretty_(self, printer, cycle):
        printer.text(repr(self))


class DeltaChildren(Sequence):
    def __init__(self, delta, parent):
        self.delta, self.parent = delta, parent

    def __len__(self):
        self.delta._guard()
        return self.delta.db.execute('SELECT count(*) FROM links WHERE parent=?', (self.parent,)).fetchone()[0]

    def __iter__(self):
        self.delta._guard()
        for position, count in self.delta.db.execute('SELECT child,n FROM links WHERE parent=? ORDER BY child', (self.parent,)):
            f = self.delta._family(self.delta.after, position)
            yield {'id': f.id, 'depth': self.delta.stop, 'count': count, 'structure': f.structure}

    def __getitem__(self, position):
        if isinstance(position, slice):
            return tuple(self[i] for i in range(*position.indices(len(self))))
        if position < 0:
            position += len(self)
        if not 0 <= position < len(self):
            raise IndexError(position)
        return next(islice(self, position, position+1))


def family_statistics(frame):
    """Count-only aggregation; never create per-family numerical profiles."""
    frame._owner.model
    if len(frame) == frame._store.total:
        total, largest, singletons = frame._store.db.execute(
            'SELECT coalesce(sum(n),0),coalesce(max(n),0),coalesce(sum(n=1),0) FROM families'
        ).fetchone()
        top5 = frame._store.db.execute(
            'SELECT coalesce(sum(n),0) FROM (SELECT n FROM families ORDER BY position LIMIT 5)'
        ).fetchone()[0]
    else:
        from heapq import heappush
        from heapq import heappushpop
        total = largest = singletons = 0
        top = []
        for position in frame.ids:
            n = frame._store.db.execute('SELECT n FROM families WHERE position=?', (position,)).fetchone()[0]
            total += n
            largest = max(largest, n)
            singletons += n == 1
            if len(top) < 5:
                heappush(top, n)
            else:
                heappushpop(top, n)
        top5 = sum(top)
    return {'families': len(frame), 'members': total, 'largest_count': largest,
            'largest_coverage': Fraction(largest, total) if total else Fraction(0),
            'top5_count': top5, 'top5_coverage': Fraction(top5, total) if total else Fraction(0),
            'singleton_families': singletons}


def family_table(frame, *, limit=15):
    """A prefix preview with a local cap and truthful selected-member coverage."""
    _Frame._size(limit)
    stats = family_statistics(frame)
    shown = min(len(frame), limit, options.display.max_rows)
    is_row = frame._store.kind == 'row'
    kind, unit = ('RowFamilies', 'rows') if is_row else ('VariableRoles', 'variables')
    title = (f'<{kind} depth={frame._store.depth}, mode={frame._store.result.mode}: '
             f'showing {shown:,} of {len(frame):,} families; covering {stats["members"]:,} selected {unit}>')
    def record_at(i):
        record = dict(frame[i])
        record['count'] = f'{record["count"]:,}'
        if is_row:
            record['nnz'] = degree_text(record['nnz'])
        else:
            record['row_degree'] = degree_text(record['row_degree'])
        return record
    body = _table(title, frame.columns, range(shown), record_at)
    covered = sum(frame._store.record(frame.ids[i])[2] for i in range(shown))
    percent = float(Fraction(100*covered, stats['members'])) if stats['members'] else 0.0
    from mipframe.ir.signatures import _ColorOrder
    order = 'selection order'
    if is_row and isinstance(frame.ids, range) and frame.ids.step > 0:
        order = 'count descending'
    elif not is_row and isinstance(frame.ids, _ColorOrder) and frame.ids.ordinals.step > 0:
        order = 'role ID'
    lines = [body, f'Shown {shown:,} families cover {covered:,}/{stats["members"]:,} {unit} ({percent:.1f}%); order: {order}.']
    if shown < len(frame):
        lines.append(f'{len(frame)-shown:,} families omitted; use .show(limit=...) or slices to inspect more.')
    if len(frame) < frame._total:
        lines.append(f'Selection from {frame._total:,} families in this query.')
    if is_row:
        lines.append('Signed role groups; × = term multiplicity; n# modes are per signed role. rhs_class follows canonical orientation.')
    else:
        lines.append('row_degree = original constraint incidence count per variable (range if varying).')
    return '\n'.join(lines)


class StructuralSummary:
    def __init__(self, result, depth, *, max_families=15, max_splits=5):
        validate_summary_limits(max_families, max_splits)
        self.result, self.depth = result, depth
        self.max_families, self.max_splits = max_families, max_splits

    @property
    def levels(self):
        self.result._store.guard()
        levels = []
        for d in range(self.depth+1):
            rows, variables = len(self.result._row_families(d)), len(self.result._var_families(d))
            levels.append({'depth': d, 'row_roles': rows, 'var_roles': variables,
                           'delta_rows': None if not d else rows-levels[-1]['row_roles'],
                           'delta_vars': None if not d else variables-levels[-1]['var_roles']})
        return tuple(levels)

    @property
    def variable_roles(self):
        return self.result._var_families(max(0, self.depth-1))

    @property
    def row_families(self):
        return self.result._row_families(self.depth)

    @property
    def delta(self):
        return None if self.depth == 0 else self.result.splits(depth=self.depth)

    @property
    def variable_delta(self):
        # Explain the variable legend consumed by the displayed row signatures.
        return None if self.depth < 2 else self.result.splits(depth=self.depth-1, kind='var')

    @property
    def concentration(self):
        return family_statistics(self.row_families)

    def __repr__(self):
        owner = self.result._store.owner
        rows, variables = owner.shape
        levels = self.levels
        def level_at(i):
            record = dict(levels[i])
            for key in ('delta_rows', 'delta_vars'):
                value = record[key]
                record[key] = None if value is None else f'+{value}' if value > 0 else str(value)
            return record
        # Keep both the earliest levels and requested terminal level in a long run.
        limit = min(self.max_families, options.display.max_rows)
        indices = list(range(len(levels))) if len(levels) <= limit else [*range(limit-1), len(levels)-1]
        table = _table('Depth overview', ('depth', 'row_roles', 'delta_rows', 'var_roles', 'delta_vars'), indices,
                       lambda i: level_at(indices[i]))
        if len(indices) < len(levels):
            table += f'\n{len(levels)-len(indices):,} intermediate depth levels omitted.'
        stats = self.concentration
        concentration = (f'Row concentration: largest {stats["largest_count"]:,}/{rows:,} ({float(100*stats["largest_coverage"]):.1f}%); '
                         f'top-5 coverage {float(100*stats["top5_coverage"]):.1f}%; singleton families {stats["singleton_families"]:,}.')
        lines = [f'Structural summary ({self.result.mode}): {rows:,} rows, {variables:,} variables', table, concentration,
                 family_table(self.variable_roles, limit=self.max_families)]
        variable_delta = self.variable_delta
        if variable_delta is not None:
            lines.append('Variable refinement explaining the legend:\n' + variable_delta.render(
                max_splits=self.max_splits, max_children=self.max_splits, details=False))
        lines.append(family_table(self.row_families, limit=self.max_families))
        if self.depth:
            lines.append(self.delta.render(max_splits=self.max_splits, max_children=self.max_splits))
        return '\n\n'.join(lines)

    def _repr_pretty_(self, printer, cycle):
        printer.text(repr(self))
