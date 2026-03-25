# Pipeline Builder

The Pipeline Builder is a helper for constructing transactional pipelines in Transactron.
It lets you combine external methods, function-based stages, and method calls into a single flow with automatic data forwarding.

## Overview

A pipeline is an ordered sequence of stages where:
- each stage consumes some live signals,
- may produce new signals,
- and passes the resulting live state to the next stage.

Main use cases:
- datapath pipelines,
- arithmetic/micro-op sequencing,
- integrating multiple transactional blocks into a linear flow.

## Basic Concepts

### Node Types

Pipelines can contain three node kinds:
1. `add_external(method, ...)`: pipeline provides (defines) a method body.
2. `call_method(method, ...)`: pipeline calls an existing method.
3. `@stage(...)`: function-based stage converted to a transactional method.

### Live Signals

At each position in the pipeline, a set of live signals is tracked.
Stages can consume any currently live signals they declare as inputs and add/overwrite outputs.

`PipelineBuilder.get_live_signals()` can be used to inspect this state before elaboration.

## Happens-Before Semantics

By default, pipeline stages are connected with strict happens-before relationships.
If stage `A` is before stage `B`, then `A` must complete before `B` can start.

This is usually what you want, but it can create deadlocks in certain dependency patterns.
Typical problematic case:
- `A` and `B` are external/provided stages,
- an external module attempts to call both in one transaction,
- `A` waits on `B` readiness while `B` is constrained by ordering.

In that case, the strict ordering can prevent progress.

## `no_dependency` Semantics

`no_dependency=True` weakens the default ordering for a node.

What it does:
- the stage is decoupled from prior pipeline dependency ordering,
- it may execute before earlier-stage data arrives,
- this can break the deadlock pattern described above.

Important constraint:
- a `no_dependency` node cannot require any input signals from earlier stages.
- in practice, such a node must have an empty required/input set.

Use it sparingly and only when you intentionally want to break strict happens-before constraints.

## Quick Start

```python
from amaranth import *
from transactron import Method, TModule
from transactron.lib.pipeline import PipelineBuilder


class SimplePipeline(Elaboratable):
    def __init__(self):
        self.write = Method(i=[("x", 32)])
        self.read = Method(o=[("result", 32)])

    def elaborate(self, platform):
        m = TModule()
        m.submodules.pipeline = p = PipelineBuilder()

        p.add_external(self.write)

        @p.stage(m, o=[("result", 32)])
        def _(x):
            return {"result": x + 10}

        p.add_external(self.read)
        return m
```

## API Notes

### Constructor

```python
p = PipelineBuilder(allow_unused=False, allow_empty=False)
```

- `allow_unused`: allow generated fields that are never consumed later.
- `allow_empty`: allow points where no live signals exist.

### Stage API

```python
@p.stage(m, o=[("result", ...)])
def _(foo, bar):
    return {"result": ...}
```

- `i=None` means input layout is inferred from function parameters.
- `name` overrides default generated stage method name.
- extra keyword args are forwarded to `call_method` (`ready`, `no_dependency`, `src_loc`).
- can call other methods

### FIFO Decoupling

```python
p.fifo(depth=16)
```

Inserts a FIFO between current and next stage.
Current implementation uses `BasicFifo` internally.

## Example: Pipelined Multiplier

The following complete example demonstrates a multi-stage arithmetic pipeline using `PipelineBuilder`:

```{literalinclude} _code/pipelined_mult.py
```

## Debugging Tips

```python
live = p.get_live_signals()
for idx, signals in enumerate(live):
    print(idx, signals)
```

Common failures:
- missing input signal: stage references a signal not live at that point,
- shape mismatch: required signal shape differs from produced shape,
- empty live region when `allow_empty=False`,
- `no_dependency=True` on a stage that still requires inputs.

## See Also

- [Getting started](getting-started.md)
- [Transactions and methods](transactions.md)
- [API documentation](api.md)
