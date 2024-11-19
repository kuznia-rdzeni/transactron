# Development environment

## Setting up

In order to prepare the development environment, please follow the steps below:

1. Install the Python 3.11 interpreter and pip package manager.
    * Optionally create a Python virtual environment with `python3 -m venv venv` in the project directory and activate it using generated script: `. venv/bin/activate`.
2. Install all required libraries with `pip3 install .[dev]`.
4. Optionally, install all precommit hooks with `pre-commit install`. This will automatically run the linter before commits.

## Running tests

Tests are run using `pytest`. Useful things to know are:

* To speed up running tests, multiple worker threads can be used -- use option `-n auto`.
* To run only some tests, use the option `-k EXPRESSION`. Expression format is described in pytest docs.
* To be able to read the standard output even for successful tests, use `-s`.

Custom options are:

* `--transactron-traces` -- generates trace files readable by `gtkwave`. The trace files are saved in `test/__traces__`.
* `--transactron-profile` -- generates Transactron execution profiles, readable by the script named `tprof.py`. The profiles are saved in `test/__profiles__`.
* `--transactron-log-filter` -- allows to filter test logs.

## Using scripts

The development environment contains a number of scripts which are run in CI, but are also intended for local use. They are:

### lint.sh

Checks the code formatting and typing. It should be run as follows:

```
scripts/lint.sh subcommand [filename...]
```

The following main subcommands are available:

* `format` -- reformats the code using `black`.
* `check_format` -- verifies code formatting using `black` and `flake8`.
* `check_types` -- verifies typing using `pyright`.
* `verify` -- runs all checks. The same set of checks is run in CI.

When confronted with `would reformat [filename]` message from `black` you may run:

```
black --diff [filename]
```
This way you may display the changes `black` would apply to `[filename]` if you chose the `format` option for `lint.sh` script. This may help you locate the formatting issues.

### build\_docs.sh

Generates local documentation using [Sphinx](https://www.sphinx-doc.org/). The generated HTML files are located in `build/html`.

### tprof.py

Processes Transactron profile files and presents them in a readable way.
To generate a profile file, the `run_tests.py` script should be used with the `--profile` option.
The `tprof.py` can then be run as follows:

```
scripts/tprof.py test/__profile__/profile_file.json
```

This displays the profile information about transactions by default.
For method profiles, one should use the `--mode=methods` option.

The columns have the following meaning:

* `name` -- the name of the transaction or method in question. The method names are displayed together with the containing module name to differentiate between identically named methods in different modules.
* `source location` -- the file and line where the transaction or method was declared. Used to further disambiguate transaction/methods.
* `locked` -- for methods, shows the number of cycles the method was locked by the caller (called with a false condition). For transactions, shows the number of cycles the transaction could run, but was forced to wait by another, conflicting, transaction.
* `run` -- shows the number of cycles the given method/transaction was running.

To display information about method calls, one can use the `--call-graph` option.
When displaying transaction profiles, this option produces a call graph. For each transaction, there is a tree of methods which are called by this transaction.
Counters presented in the tree shows information about the calls from the transaction in the root of the tree: if a method is also called by a different transaction, these calls are not counted.
When displaying method profiles, an inverted call graph is produced: the transactions are in the leaves, and the children nodes are the callers of the method in question.
In this mode, the `locked` field in the tree shows how many cycles a given method or transaction was responsible for locking the method in the root.

Other options of `tprof.py` are:

* `--sort` -- selects which column is used for sorting rows.
* `--filter-name` -- filters rows by name. Regular expressions can be used.
* `--filter-loc` -- filters rows by source locations. Regular expressions can be used.
