"""Importing this subpackage registers every implemented condition-family
compiler with ``core.py``'s dispatch tables (see each module's own
docstring for its grammar)."""

from . import status_control  # noqa: F401
from . import schedule  # noqa: F401
from . import triggerref  # noqa: F401
from . import inet  # noqa: F401
from . import var_condition  # noqa: F401
from . import x10_condition  # noqa: F401
