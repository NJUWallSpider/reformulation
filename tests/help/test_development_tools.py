"""Release and opt-in corpus checks do not silently claim skipped work passed."""
import copy
import hashlib
import json
from pathlib import Path
import runpy

import pytest
import mipframe as mf
from mipframe._help.query import corpus

ROOT = Path(__file__).resolve().parents[2]


def test_missing_external_snapshots_are_not_pass(tmp_path):
    harness = runpy.run_path(str(ROOT/'scripts/evaluate_help_cases.py'))
    result = harness['run'](tmp_path, list(harness['CASES']))
    assert all(r['status'] == 'NOT_RUN' for r in result['results'])
    assert result['online_agent_evaluation'] == 'NOT_RUN'
    assert not list(tmp_path.iterdir())


def test_development_removed_binding_warns(monkeypatch):
    monkeypatch.delattr(mf.Frame, 'join')
    data = mf.help('Frame.join', format='json')
    assert data['warnings'] and data['results'][0]['available'] is False
    assert 'unavailable' in data['results'][0]['signatures']['Frame.join']


def test_checksum_rejects_corrupt_installed_resource(monkeypatch):
    import mipframe._help.query as query
    original = corpus()
    damaged = copy.deepcopy(original)
    damaged['topics'][0]['summary'] = 'tampered'
    class File:
        def joinpath(self, name): return self
        def read_text(self, **kwargs): return json.dumps(damaged)
    corpus.cache_clear()
    monkeypatch.setattr(query, 'files', lambda *a: File())
    try:
        with pytest.raises(RuntimeError, match='checksum'):
            mf.help()
    finally:
        corpus.cache_clear()


def test_release_validator_rejects_source_or_content_drift(tmp_path, monkeypatch):
    from mipframe._help import maintenance
    package = tmp_path/'mipframe'
    (package/'_help').mkdir(parents=True)
    (package/'__init__.py').write_text('__version__ = "0.6.0"\n')
    data = copy.deepcopy(corpus())
    (package/'_help/catalog.json').write_text(json.dumps(data))
    monkeypatch.setattr(maintenance, 'source_hash', lambda p: 'changed')
    monkeypatch.setattr(maintenance, 'content_hash', lambda p: data['content_hash'])
    with pytest.raises(RuntimeError, match='Documentation mismatch'):
        maintenance.validate(package)


def test_skill_has_valid_copyable_frontmatter():
    text = (ROOT/'docs/agent/SKILL.md').read_text()
    assert text.startswith('---\nname: mipframe\n')
    assert '\ndescription: ' in text.split('---', 2)[1]
    assert '{{version}}' not in text and mf.__version__ in text


def test_unknown_objects_do_not_evaluate_properties():
    class Hostile:
        @property
        def __class__(self):
            raise AssertionError('Help inspected an instance property')
        def __repr__(self):
            raise AssertionError('Help rendered an instance')
    with pytest.raises(TypeError, match='Expected a help query'):
        mf.help(Hostile())
