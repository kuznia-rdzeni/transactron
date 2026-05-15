import pytest
from .test_case import TestCaseWithSimulatorBase


__all__ = ["TestCaseWithSimulator"]


class TestCaseWithSimulator(TestCaseWithSimulatorBase):
    @pytest.fixture(autouse=True)
    def fixture_initialize_testing_env(self, request):
        # Hypothesis creates a single instance of a test class, which is later reused multiple times.
        # This means that pytest fixtures are only run once. We can take advantage of this behaviour and
        # initialise hypothesis related variables. This is done in `self.testing_env()`.

        # Base name which will be used later to create file names for particular outputs
        base_output_file_name = ".".join(request.node.nodeid.split("/"))
        with self.ctx_testing_env(base_output_file_name):
            yield
