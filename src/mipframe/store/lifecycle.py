"""Release descriptors before directory cleanup, including failed construction."""

import atexit
import sys
import weakref


_pending = {}


def retry_cleanup():
    for name, temp in tuple(_pending.items()):
        try:
            temp.cleanup()
        except OSError:
            continue
        _pending.pop(name, None)


atexit.register(retry_cleanup)


def register(owner, resource):
    if not hasattr(owner, '_query_stores'):
        owner._query_stores = weakref.WeakSet()
    owner._query_stores.add(resource)


def release_all(actions):
    """Attempt every release; preserve an active error and annotate cleanup errors."""
    active = sys.exception()
    errors = []
    for action in actions:
        try:
            action()
        except Exception as error:
            errors.append(error)
    if errors:
        primary = active or errors.pop(0)
        for error in errors:
            primary.add_note(f'Resource cleanup also failed: {error}')
        if active is None:
            raise primary


def cleanup_directory(temp):
    if temp is not None:
        try:
            temp.cleanup()
        except OSError:
            _pending[temp.name] = temp
            raise


def close_connection(store):
    db = getattr(store, "db", None)
    if db is not None:
        db.close()
        store.db = None


def close_store(store):
    """Keep the original failure if cleanup also fails; retry failed directories."""
    active = sys.exception()
    try:
        close_connection(store)
        retry_cleanup()
        temp = getattr(store, "temp", None)
        if temp is not None:
            try:
                temp.cleanup()
            except OSError:
                _pending[temp.name] = temp
                raise
            finally:
                store.temp = None
    except Exception as exc:
        if active is None:
            raise
        active.add_note(f"Storage cleanup also failed: {exc}")


def finalize_store(store):
    try:
        store.close()
    except Exception:
        # Explicit close reports failures. Destructors must not emit unraisable
        # exceptions, particularly while recovering from descriptor exhaustion.
        pass
