"""Verify an internal wheel and run tests from its companion source distribution."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import zipfile

from verify_help_distribution import run as verify_installed_help


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheel', type=Path, required=True)
    parser.add_argument('--sdist', type=Path, required=True)
    args = parser.parse_args()
    wheel, sdist = args.wheel.resolve(), args.sdist.resolve()
    with zipfile.ZipFile(wheel) as package:
        names = package.namelist()
        assert all(n.startswith(('mipframe/', 'mipframe-')) for n in names), names
        metadata = package.read(next(n for n in names if n.endswith('.dist-info/METADATA'))).decode()
        assert 'Classifier: Private :: Do Not Upload' in metadata
        assert 'mipframe/_help/catalog.json' in names
        assert not any('__pycache__' in n or n.endswith('.pyc') for n in names)
    verify_installed_help(wheel)
    with tempfile.TemporaryDirectory(prefix='mipframe-release-') as temporary:
        work = Path(temporary)
        with tarfile.open(sdist) as package:
            for item in package.getmembers():
                path = Path(item.name)
                if path.is_absolute() or '..' in path.parts or item.issym() or item.islnk():
                    raise ValueError(f'Unsafe source archive member: {item.name}')
            package.extractall(work, filter='data')
        roots = list(work.iterdir())
        assert len(roots) == 1
        root = roots[0]
        assert not any((root / name).exists() for name in ('data', 'literature', 'evaluation', 'practice', 'playground'))
        env = dict(os.environ, PYTHONPATH=str(root/'src'),
                   MIPFRAME_MODEL_DATA_ROOT=str(work/'absent-models'))
        env.pop('MIPFRAME_EXTERNAL_DATA_ROOT', None)
        subprocess.run([sys.executable, 'scripts/build_help.py', '--check'], cwd=root, env=env, check=True)
        subprocess.run([sys.executable, '-m', 'pytest', '-q', '-rs'], cwd=root, env=env, check=True)
        # Exercise the installed wheel, with the source tree removed from import paths.
        installed = work/'installed'
        subprocess.run([sys.executable, '-m', 'pip', 'install', '--no-index', '--no-deps',
                        '--target', str(installed), str(wheel)], check=True)
        env['PYTHONPATH'] = str(installed)
        for example in ('basic_read.py', 'save_reload.py', 'exact_certificates.py', 'exact_edits.py', 'structural_ops.py', 'discrete_search.py'):
            subprocess.run([sys.executable, str(root/'examples'/example)], cwd=work, env=env, check=True)
    print(json.dumps({'status': 'PASS', 'artifacts': [
        {'file': p.name, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest(), 'bytes': p.stat().st_size}
        for p in (wheel, sdist)]}, indent=2))


if __name__ == '__main__':
    main()
