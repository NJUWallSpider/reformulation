"""Build and exercise installed help outside the checkout, offline and read-only."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def run(wheel=None):
    with tempfile.TemporaryDirectory(prefix='mipframe-help-install-') as temporary:
        work = Path(temporary)
        if wheel is None:
            subprocess.run([sys.executable, '-m', 'build', '--no-isolation', '--wheel',
                            '--outdir', str(work/'dist'), str(ROOT)], check=True)
            wheel = next((work/'dist').glob('*.whl'))
        installed = work/'installed'
        subprocess.run([sys.executable, '-m', 'pip', 'install', '--no-index', '--no-deps',
                        '--no-compile', '--target', str(installed), str(wheel)], check=True)
        (work/'sitecustomize.py').write_text('''import socket
def forbidden(*args, **kwargs):
    raise AssertionError("Network access is disabled in the installed-help probe")
socket.socket.connect = forbidden
socket.socket.connect_ex = forbidden
socket.create_connection = forbidden
''')
        env = dict(os.environ, PYTHONPATH=os.pathsep.join((str(work), str(installed))),
                   PYTHONDONTWRITEBYTECODE='1', HOME=str(work/'empty-home'))
        (work/'empty-home').mkdir()
        for p in installed.rglob('*'):
            p.chmod(0o555 if p.is_dir() else 0o444)
        installed.chmod(0o555)
        try:
            probe = '''
import json, pathlib, socket, sys
try: socket.create_connection(("example.com", 80))
except AssertionError: pass
else: raise AssertionError("Network restriction did not apply")
import mipframe as mf
assert pathlib.Path(mf.__file__).resolve().is_relative_to(pathlib.Path(sys.argv[1]).resolve())
def forbidden(*a, **kw): raise AssertionError("Help attempted model execution")
mf.read = mf.load = forbidden
assert mf.help("recipe.coordinates", format="json")["results"][0]["id"] == "recipe.coordinates"
for query, wanted in [
    ("many linear certificates", "api.Checker.linear_batch"),
    ("Frame native batch certificates", "api.Checker.linear_many"),
    ("how to tighten a big-M coefficient", "concept.big-m"),
    ("find a minimum weighted set cover", "concept.resource-cover"),
    ("fractional point feasibility", "limitation.fractional-point"),
    ("report resource lifecycle", "concept.report-resources"),
]:
    assert mf.help(query, limit=1, format="json")["results"][0]["id"] == wanted
assert mf.help("api.Report.close", format="json")["results"] == []
assert mf.help(mf.Frame.join, format="json")["results"]
assert "MIPFrame" in str(mf.help("skill", detail="full")) or "mipframe" in str(mf.help("skill", detail="full"))
assert mf.help(format="json")["results"]
print(json.dumps({"installed":mf.__file__,"version":mf.__version__,"offline_read_only":"PASS"}))
'''
            subprocess.run([sys.executable, '-c', probe, str(installed)], cwd=work, env=env, check=True)
            for topic, options in [('support containment', ['--format', 'json']),
                                   ('skill', ['--detail', 'full'])]:
                completed = subprocess.run([sys.executable, '-m', 'mipframe', 'help', topic, *options],
                                           cwd=work, env=env, check=True, capture_output=True, text=True)
                assert completed.stdout and not completed.stderr
            assert not list((work/'empty-home').iterdir()), 'Help wrote a cache in HOME'
            assert not list(installed.rglob('__pycache__')), 'Help wrote bytecode in the installation'
        finally:
            installed.chmod(0o755)
            for p in installed.rglob('*'):
                p.chmod(0o755 if p.is_dir() else 0o644)
        print('Installed Python, CLI and Skill: PASS (network disabled, read-only package, external cwd)')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheel', type=Path)
    args = parser.parse_args()
    run(args.wheel.resolve() if args.wheel else None)
