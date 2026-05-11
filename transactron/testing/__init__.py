from amaranth.sim._async import TestbenchContext, ProcessContext, SimulatorContext  # noqa: F401
from transactron.utils import data_layout  # noqa: F401

# .input_generation not reexported because of namespace pollution and optional dependency
# .test_case lazily imported because of optional dependency
from .functions import *  # noqa: F401
from .simulator import *  # noqa: F401
from .test_circuit import *  # noqa: F401
from .method_mock import *  # noqa: F401
from .testbenchio import *  # noqa: F401
from .profiler import *  # noqa: F401
from .logging import *  # noqa: F401
from .tick_count import *  # noqa: F401


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .test_case import TestCaseWithSimulator  # noqa: F401

del TYPE_CHECKING


def __getattr__(name):
    if name in ["TestCaseWithSimulator"]:
        module = __import__(".test_case", fromlist=[name])
        return getattr(module, name)
    raise AttributeError(f"module {__name__} has no attribute {name}")
