"""Shared signature rendering for compiled and live help contracts."""
import inspect
import re


def signature(obj, *, constructor=False):
    if isinstance(obj, property):
        return ' [property]'
    if inspect.isclass(obj) and not constructor:
        return ' [type]'
    sig = inspect.signature(obj)
    params = [p for name, p in sig.parameters.items()
              if name not in ('self', 'cls', 'ctx') and not name.startswith('_')]
    return re.sub(r'<object object at 0x[0-9a-f]+>', '<unchanged>',
                  str(sig.replace(parameters=params)))
