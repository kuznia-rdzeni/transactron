import sys
import logging
import os
import functools
from contextlib import contextmanager
from collections.abc import Callable
from typing import Concatenate, Optional
from amaranth import *
from amaranth.sim import *
from amaranth_types import HasElaborate

from transactron.utils.dependencies import DependencyContext, DependencyManager
from .profiler import profiler_process, Profile
from .logging import make_logging_process, parse_logging_level, _LogFormatter
from .tick_count import make_tick_count_process
from .simulator import PysimSimulator, tick, random_wait, random_wait_geom
from transactron.core.keys import TransactionManagerKey


__all__ = ["TestCaseWithSimulatorBase"]


class TestCaseWithSimulatorBase:
    dependency_manager: DependencyManager

    @contextmanager
    def _configure_dependency_context(self):
        self.dependency_manager = DependencyManager()
        with DependencyContext(self.dependency_manager):
            yield Tick()

    def _add_class_mocks(self, sim: PysimSimulator) -> None:
        for key in dir(self):
            val = getattr(self, key)
            if hasattr(val, "_transactron_testing_process"):
                sim.add_process(val)
            elif hasattr(val, "_transactron_method_mock"):
                sim.add_mock(val())

    def _add_local_mocks(self, sim: PysimSimulator, frame_locals: dict) -> None:
        for key, val in frame_locals.items():
            if hasattr(val, "_transactron_testing_process"):
                sim.add_process(val)
            elif hasattr(val, "_transactron_method_mock"):
                sim.add_mock(val())

    def _add_all_mocks(self, sim: PysimSimulator, frame_locals: dict) -> None:
        self._add_class_mocks(sim)
        self._add_local_mocks(sim, frame_locals)

    def _configure_traces(self):
        traces_file = None
        if "__TRANSACTRON_DUMP_TRACES" in os.environ:
            traces_file = self._transactron_current_output_file_name
        self._transactron_infrastructure_traces_file = traces_file

    @contextmanager
    def _configure_profiles(self):
        profile = None
        if "__TRANSACTRON_PROFILE" in os.environ:

            def f():
                nonlocal profile
                try:
                    transaction_manager = DependencyContext.get().get_dependency(TransactionManagerKey())
                    profile = Profile()
                    return profiler_process(transaction_manager, profile)
                except KeyError:
                    pass
                return None

            self._transactron_sim_processes_to_add.append(f)

        yield

        if profile is not None:
            profile_dir = "test/__profiles__"
            profile_file = self._transactron_current_output_file_name
            os.makedirs(profile_dir, exist_ok=True)
            profile.encode(f"{profile_dir}/{profile_file}.json")

    @contextmanager
    def _configure_logging(self):
        def on_error():
            assert False, "Simulation finished due to an error"

        log_level = parse_logging_level(os.environ["__TRANSACTRON_LOG_LEVEL"])
        log_filter = os.environ["__TRANSACTRON_LOG_FILTER"]
        self._transactron_sim_processes_to_add.append(lambda: make_logging_process(log_level, log_filter, on_error))

        ch = logging.StreamHandler()
        formatter = _LogFormatter()
        ch.setFormatter(formatter)

        root_logger = logging.getLogger()
        handlers_before = root_logger.handlers.copy()
        root_logger.handlers.append(ch)
        yield
        root_logger.handlers = handlers_before

    @contextmanager
    def ctx_testing_env_next(self):
        # File name to be used in the current test run (either standard or hypothesis iteration)
        # for standard tests it will always have the suffix "_0". For hypothesis tests, it will be suffixed
        # with the current hypothesis iteration number, so that each hypothesis run is saved to a
        # the different file.
        self._transactron_current_output_file_name = (
            self._transactron_base_output_file_name + "_" + str(self._transactron_hypothesis_iter_counter)
        )
        self._transactron_sim_processes_to_add: list[Callable[[], Optional[Callable]]] = []
        with self._configure_dependency_context():
            self._configure_traces()
            with self._configure_profiles():
                with self._configure_logging():
                    self._transactron_sim_processes_to_add.append(make_tick_count_process)
                    yield
        self._transactron_hypothesis_iter_counter += 1

    @contextmanager
    def ctx_testing_env(self, base_output_file_name: str):
        # Hypothesis creates a single instance of a test class, which is later reused multiple times.
        # This means that pytest fixtures are only run once. We can take advantage of this behaviour and
        # initialise hypothesis related variables.

        # The counter for distinguishing between successive hypothesis iterations, it is incremented
        # by `reinitialize_fixtures` which should be started at the beginning of each hypothesis run
        self._transactron_hypothesis_iter_counter = 0
        # Base name which will be used later to create file names for particular outputs
        self._transactron_base_output_file_name = base_output_file_name
        with self.ctx_testing_env_next():
            yield

    @staticmethod
    def wrap_testing_env_next[S: "TestCaseWithSimulatorBase", T, **P](func: Callable[Concatenate[S, P], T]):
        @functools.wraps(func)
        def wrapper(self, *args: P.args, **kwargs: P.kwargs) -> T:
            with self.ctx_testing_env_next():
                return func(self, *args, **kwargs)

        return wrapper

    @staticmethod
    def wrap_testing_env(base_output_file_name: str):
        def wrap[S: "TestCaseWithSimulatorBase", T, **P](func: Callable[Concatenate[S, P], T]):
            @functools.wraps(func)
            def wrapper(self, *args: P.args, **kwargs: P.kwargs) -> T:
                with self.ctx_testing_env(base_output_file_name):
                    return func(self, *args, **kwargs)

            return wrapper

        return wrap

    @contextmanager
    def run_simulation(self, module: HasElaborate, max_cycles: float = 10e4, add_transaction_module=True):
        clk_period = 1e-6
        sim = PysimSimulator(
            module,
            max_cycles=max_cycles,
            add_transaction_module=add_transaction_module,
            traces_file=self._transactron_infrastructure_traces_file,
            clk_period=clk_period,
        )
        self._add_all_mocks(sim, sys._getframe(2).f_locals)

        yield sim

        for f in self._transactron_sim_processes_to_add:
            ret = f()
            if ret is not None:
                sim.add_process(ret)

        sim.run()

    tick = staticmethod(tick)
    random_wait = staticmethod(random_wait)
    random_wait_geom = staticmethod(random_wait_geom)
