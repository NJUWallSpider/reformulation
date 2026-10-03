"""Deterministic weighted BM25 over a small, versioned local corpus."""
import math
import re
from collections import Counter, defaultdict

STOP = frozenset('a an the to for of in on with and or is are be do does how can i my me from that this it use using find get have want would which what when'.split())
SYNONYMS = {
    'cartesian': ('coordinate', 'product'), 'indexing': ('coordinate',),
    'partition': ('set',), 'containment': ('subset',), 'contained': ('subset',),
    'decomposition': ('component', 'coupling'), 'blocks': ('component',),
    'adjacency': ('neighbor', 'term'), 'coefficient': ('coef',),
    'conflict': ('clique', 'witness'), 'precedence': ('reachability', 'closure'),
    'redundant': ('redundancy', 'certificate'), 'cut': ('inequality',),
    'rounding': ('round',), 'rational': ('exact',), 'mutate': ('edit',),
    'save': ('native', 'snapshot'), 'enumeration': ('enumerate',),
    'verification': ('verify', 'certificate'), 'verify': ('certificate',),
    'closes': ('closed',), 'symmetry': ('automorphism', 'template'), 'binary': ('integer',),
}


def tokens(text):
    words = re.findall(r'[a-z][a-z0-9]*', text.lower())
    result = []
    for w in words:
        if w in STOP:
            continue
        if len(w) > 4 and w.endswith('ies'):
            w = w[:-3] + 'y'
        elif len(w) > 4 and w.endswith('s') and not w.endswith(('ss', 'is')):
            w = w[:-1]
        result.append(w)
    return result


def index(topics):
    postings = defaultdict(list)
    lengths = {}
    for t in topics:
        c = Counter()
        for key, weight in [('title', 5), ('symbols', 6), ('aliases', 5), ('keywords', 5), ('summary', 3), ('body', 1)]:
            value = t.get(key, '')
            text = ' '.join(value) if isinstance(value, list) else value
            for word, n in Counter(tokens(text)).items():
                c[word] += n * weight
        lengths[t['id']] = sum(c.values())
        for word, n in sorted(c.items()):
            postings[word].append([t['id'], n])
    return dict(postings=dict(sorted(postings.items())), lengths=lengths)


def search(query, topics, compiled):
    words = dict.fromkeys(tokens(query))
    for word in tuple(words):
        for alias in SYNONYMS.get(word, ()):
            for token in tokens(alias):
                words.setdefault(token, None)
    scores = defaultdict(float)
    n = len(topics)
    avg = sum(compiled['lengths'].values()) / max(n, 1)
    for word in words:
        entries = compiled['postings'].get(word, ())
        idf = math.log(1 + (n - len(entries) + .5) / (len(entries) + .5))
        for ident, tf in entries:
            length = compiled['lengths'][ident]
            scores[ident] += idf * tf * 2.2 / (tf + 1.2 * (.25 + .75 * length / max(avg, 1)))
    by_id = {t['id']: t for t in topics}
    for ident in scores:
        if by_id[ident]['kind'] in ('recipe', 'capability', 'limitation', 'troubleshooting'):
            scores[ident] *= 1.2
    return sorted(scores, key=lambda ident: (-scores[ident], ident))
