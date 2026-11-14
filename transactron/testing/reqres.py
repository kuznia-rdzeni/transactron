from collections import deque
from collections.abc import Callable, Mapping
import functools
from typing import Any, Protocol

from amaranth.sim._async import SimulatorContext

from transactron.utils.transactron_helpers import mock_def_helper
from transactron.utils.typing import NameIntDict
from .functions import MethodData
from .method_mock import MethodMock, _bind_mock_args
from .testbenchio import TestbenchIO


__all__ = ["RequestResponseTestbenchIO", "call_request_response", "def_request_response_mock"]


class RequestResponseTestbenchIO(Protocol):
    """Testbench side of a `RequestResponseInterface`."""

    request: TestbenchIO
    """Testbench IO for the request method."""

    response: TestbenchIO
    """Testbench IO for the response method."""


def def_request_response_mock(
    tb_getter: RequestResponseTestbenchIO
    | Callable[[], RequestResponseTestbenchIO]
    | Callable[[Any], RequestResponseTestbenchIO],
    *,
    compute_on_response: bool = False,
    max_pending: int | None = None,
    request_enable: Callable[[], bool] | Callable[[Any], bool] | None = None,
    response_enable: Callable[[], bool] | Callable[[Any], bool] | None = None,
    delay: float = 0,
) -> Callable[[Callable[..., NameIntDict | None]], Callable[[], list[MethodMock]]]:
    """
    Decorator function to mock a whole request-response server.

    The decorated function receives the arguments of a request, in the same
    way as functions decorated with `def_method_mock`, and returns the data
    of the corresponding response. Requests are queued, and responses are
    returned in the order of the requests. A response is available no
    earlier than in the clock cycle after its request.

    This decorator can be applied to function definitions or method
    definitions, like `def_method_mock`. Mocks defined at class level or at
    test level are automatically discovered.

    Parameters
    ----------
    tb_getter : RequestResponseTestbenchIO | Callable[[], RequestResponseTestbenchIO] \
        | Callable[[Any], RequestResponseTestbenchIO]
        Function to get the testbenches of the mocked request and response
        methods, e.g. a `RequestResponseInterface` member of a `SimpleTestCircuit`.
    compute_on_response : bool
        If false, the decorated function is called when a request is accepted,
        and its result is returned later as the response. If true, the request
        arguments are stored and the decorated function is called when the
        response is taken.
    max_pending : int | None
        Maximum number of requests waiting for a response. When reached,
        the request method is disabled. Unlimited if `None`.
    request_enable : Callable[[], bool] | Callable[[Any], bool] | None
        Function which decides if the request method is enabled in a given
        clock cycle.
    response_enable : Callable[[], bool] | Callable[[Any], bool] | None
        Function which decides if the response method is enabled in a given
        clock cycle, when there is a pending request.
    delay : float
        Simulation time delay for method mock calling. Used for synchronization
        between different mocks and testbench processes.

    Example
    -------
    ```
    @def_request_response_mock(lambda self: self.dut.server)
    def server(self, addr):
        return {"data": self.memory[addr]}
    ```
    """

    def decorator(func: Callable[..., NameIntDict | None]) -> Callable[[], list[MethodMock]]:
        @functools.wraps(func)
        def mock(func_self=None, /) -> list[MethodMock]:
            tb, f, kw = _bind_mock_args(
                func_self, tb_getter, func, {"request_enable": request_enable, "response_enable": response_enable}
            )
            req_enable = kw["request_enable"] or (lambda: True)
            resp_enable = kw["response_enable"] or (lambda: True)

            queue: deque[Any] = deque()

            def request_fn(arg: Mapping[str, Any]) -> None:
                entry = arg if compute_on_response else mock_def_helper(tb.request, f, arg)

                @MethodMock.effect
                def _():
                    queue.append(entry)

            def response_fn() -> NameIntDict | None:
                entry = queue[0]

                @MethodMock.effect
                def _():
                    queue.popleft()

                return mock_def_helper(tb.response, f, entry) if compute_on_response else entry

            return [
                MethodMock(
                    tb.request.adapter,
                    request_fn,
                    enable=lambda: req_enable() and (max_pending is None or len(queue) < max_pending),
                    delay=delay,
                ),
                MethodMock(
                    tb.response.adapter,
                    response_fn,
                    enable=lambda: bool(queue) and resp_enable(),
                    delay=delay,
                ),
            ]

        mock._transactron_method_mock = 1  # type: ignore
        return mock

    return decorator


async def call_request_response(
    sim: SimulatorContext, port: RequestResponseTestbenchIO, data: dict[str, Any] = {}, /, **kwdata: Any
) -> MethodData:
    """Issue a request and wait for its response.

    This is not pipelined: the next request can be issued only after this call
    returns. To have multiple requests in flight, call the `request` and
    `response` methods from separate processes.

    Parameters
    ----------
    sim : SimulatorContext
        Amaranth simulator context.
    port : RequestResponseTestbenchIO
        Testbenches of the request and response methods.
    data : dict[str, Any]
        Request arguments stored in a dict.
    **kwdata : Any
        Request arguments passed as keyword arguments. If keyword arguments
        are used, the `data` argument should not be provided.
    """
    await port.request.call(sim, data, **kwdata)
    return await port.response.call(sim)
