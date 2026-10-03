"""Fail closed if a release contains stale help contracts; no build-time NumPy needed."""
from pathlib import Path
import runpy
from setuptools import setup
from setuptools.command.build_py import build_py
from setuptools.command.sdist import sdist


def validate_help():
    root = Path(__file__).parent
    checker = runpy.run_path(str(root / 'src/mipframe/_help/maintenance.py'))
    checker['validate'](root / 'src/mipframe')


class CheckedBuild(build_py):
    def run(self):
        validate_help()
        super().run()


class CheckedSdist(sdist):
    def run(self):
        validate_help()
        super().run()


setup(cmdclass={'build_py': CheckedBuild, 'sdist': CheckedSdist})
