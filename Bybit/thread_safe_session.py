import threading
from functools import wraps

class ThreadSafeSession:
    def __init__(self, obj):
        self._obj = obj
        self._lock = threading.RLock()

    def __getattr__(self, name):
        attr = getattr(self._obj, name)

        if callable(attr):
            @wraps(attr)
            def synchronized_method(*args, **kwargs):
                with self._lock:
                    return attr(*args, **kwargs)
            return synchronized_method
        else:
            with self._lock:
                return attr

    def __setattr__(self, name, value):
        if name in ('_obj', '_lock'):
            super().__setattr__(name, value)
        else:
            with self._lock:
                setattr(self._obj, name, value)

    def __delattr__(self, name):
        with self._lock:
            delattr(self._obj, name)
