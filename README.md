# Transactron

Transactron is a library for [Amaranth HDL](https://amaranth-lang.org/) which makes designing complex digital designs easier.
It provides an abstraction for ready/valid handshake signals, automatically generates arbitration circuits, and presents an intuitive, object-oriented-like interface to the circuit designer.
The main advantages of using Transactron are:

* Abstracted handshake signalling makes it easier to design complex, latency-insensitive, pipelined circuits.
* Object-oriented-like interface allows to encapsulate complex behavior of a module and present a simple interface for its users.
  The syntax used is friendly for people coming from software development background.
* Thanks to auto-generated arbitrators and a rich library of reusable components, refactoring circuits is powerful and requires less effort.
  For example, when constructing pipelines, switching between lock-step, FIFO-coupled or combinational connection is as simple as switching a connector module.

A Transactron module defines a number of *methods*, which represent actions which can be performed by the circuit.
If an action cannot be performed at a given time (e.g. a pop from an empty FIFO queue), the method is marked as not ready.
A method can be called by other methods or by *transactions*, which represent single cycle state changes in a circuit.
A given transaction can only be run when every method it calls is ready.
When two different transactions call the same method, they are in *conflict*, which indicates a structural hazard.
Transactron ensures that two conflicting transactions never run in the same clock cycle.

Transactron is inspired by [Bluespec](https://github.com/B-Lang-org/bsc) and its concept of guarded atomic actions.
However, while Bluespec requires to follow its paradigm strictly down to simple register assignments, Transactron's abstractions are intended to be used at module boundaries, while the actual logic is written in plain Amaranth.
Transactron is just a library and, as such, allows smooth interoperation with plain Amaranth HDL code.

## State of the project

The library is in alpha stage of development, but is already well tested because it serves a foundation for the [Coreblocks](https://github.com/kuznia-rdzeni/coreblocks) out-of-order RISC-V CPU.

## Documentation

The [documentation](https://kuznia-rdzeni.github.io/transactron/) is automatically generated using [Sphinx](https://www.sphinx-doc.org/).

## Contributing

Set up the [development environment](https://kuznia-rdzeni.github.io/transactron/development-environment.html) following the project documetation.

External contributors are welcome to submit pull requests for simple contributions directly.
For larger changes, please discuss your plans with us through the [issues page](https://github.com/kuznia-rdzeni/transactron/issues) or the [discussions page](https://github.com/kuznia-rdzeni/transactron/discussions) first.
This way, you can ensure that the contribution fits the project and will be merged sooner.

## License

Copyright © 2022-2024, University of Wrocław.

This project is [three-clause BSD](https://github.com/kuznia-rdzeni/transactron/blob/master/LICENSE) licensed.
