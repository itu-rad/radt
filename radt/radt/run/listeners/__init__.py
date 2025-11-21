import os
import importlib
import pkgutil

__all__ = ["listeners"]

_package_path = os.path.dirname(__file__)

listeners = {}

for finder, module_name, ispkg in pkgutil.iter_modules([_package_path]):
    if module_name.startswith("_"):
        continue
    try:
        module = importlib.import_module(f".{module_name}", __package__)
    except Exception:
        # ignore modules that fail to import to avoid breaking package import
        continue

    for attr_name in dir(module):
        attr = getattr(module, attr_name)
        if isinstance(attr, type) and "Thread" in attr_name:
            listeners[attr_name[:-6]] = attr
            globals()[attr_name] = attr
            __all__.append(attr_name)

