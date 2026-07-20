"""Importing this subpackage registers every implemented action-family
compiler with ``core.py``'s dispatch tables (see each module's own
docstring for its grammar)."""

from . import cmd_wait  # noqa: F401
from . import repeat  # noqa: F401
from . import program_control  # noqa: F401
from . import lp  # noqa: F401
from . import sys_device  # noqa: F401
from . import var_action  # noqa: F401
from . import x10_action  # noqa: F401
from . import notify  # noqa: F401
