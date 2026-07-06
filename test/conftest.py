import os
import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("transactron")
    group.addoption("--transactron-traces", action="store_true", help="Generate traces from tests.")
    group.addoption("--transactron-profile", action="store_true", help="Write execution profiles.")
    group.addoption("--transactron-evlog", action="store_true", help="Write captured event logs.")
    group.addoption("--transactron-log-filter", default=".*", action="store", help="Regexp used to filter out logs.")


def pytest_runtest_setup(item: pytest.Item) -> None:
    """
    This function is called to perform the setup phase for every test, so
    it is a perfect moment to set environment variables.
    """
    if item.config.getoption("--transactron-traces", False):  # type: ignore
        os.environ["__TRANSACTRON_DUMP_TRACES"] = "1"

    if item.config.getoption("--transactron-profile", False):  # type: ignore
        os.environ["__TRANSACTRON_PROFILE"] = "1"

    if item.config.getoption("--transactron-evlog", False):  # type: ignore
        os.environ["__TRANSACTRON_EVLOG"] = "1"

    log_filter = item.config.getoption("--transactron-log-filter")
    os.environ["__TRANSACTRON_LOG_FILTER"] = ".*" if not isinstance(log_filter, str) else log_filter

    log_level = item.config.getoption("--log-level")
    os.environ["__TRANSACTRON_LOG_LEVEL"] = "WARNING" if not isinstance(log_level, str) else log_level
