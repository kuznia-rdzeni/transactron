# Transactron

Transactron is a library for [Amaranth HDL](https://amaranth-lang.org/) which makes designing complex digital designs easier.
It is inspired by [Bluespec](https://github.com/B-Lang-org/bsc) and its concept of guarded atomic actions.
A Transactron circuit consists of a number of atomic *transactions*, which represent single cycle state changes in a circuit.
A transaction might depend on different circuit submodules via *methods*, which represent actions which can be performed by a circuit.
Transactron ensures that transactions are only performed when the used methods are ready for execution and are not simultaneously used by a different, higher priority transaction.
This mechanism allows constructing circuits which are easily composable and insensitive of latencies.

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
