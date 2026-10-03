"""Numerical model fixtures shared by parser and fidelity regressions."""

from contextlib import ExitStack

from external_models import model_path

import pytest
from mipframe import read



@pytest.fixture(scope="session")
def test1():
    with read(model_path("test1.lp")) as model:
        yield model


@pytest.fixture
def make_model(tmp_path):
    count = 0
    with ExitStack() as stack:
        def make(text, suffix=".lp", **options):
            nonlocal count
            count += 1
            source = tmp_path / f"m{count}{suffix}"
            source.write_text(text)
            return stack.enter_context(read(source, **options))

        yield make
