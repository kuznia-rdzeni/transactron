% TODO: Later, when a Transactron guide is written, add links to Transactron concepts (e.g. method, transaction, transaction manager, method readiness, transaction runnability, transaction conflicts...)

# Getting started

This tutorial serves to gently introduce basics of developing hardware using Transactron.

## Installing Transactron

To install the latest release of Transactron, run:

```
$ pip install --upgrade transactron
```

For the purpose of this tutorial, the [amaranth-boards](https://github.com/amaranth-lang/amaranth-boards) package is suggested to interact with your favorite FPGA dev board.
As `amaranth-boards` is not regularly released to `pypi`, it is recommended to install the latest development snapshot:

```
$ pip install "amaranth-boards@git+https://github.com/amaranth-lang/amaranth-boards.git"
```

## Controlling LEDs and switches

The following example demonstrates the use of Transactron for interacting with basic inputs/outputs on FPGA development boards.
It defines a circuit which allows to control a LED using a switch.
Please bear with the triviality for now: things will get more interesting later.

```{literalinclude} _code/ledcontrol1.py
```

Transactron components are standard Amaranth [elaboratables](inv:#lang-elaboration).
The main difference is that in the `elaborate` method, {py:class}`~transactron.core.tmodule.TModule` should be used instead of `Module`.

To expose an input to Transactron code, the {py:class}`~transactron.lib.basicio.InputSampler` component is added as a submodule.
To use it, a method layout must be specified, which here is an instance of {py:class}`~amaranth.lib.data.StructLayout`.
The `data` attribute then needs to be combinationally connected to the input to be exposed.
The {py:attr}`~transactron.lib.basicio.InputSampler.get` method then allows to access the input from Transactron code using a method call.
The {py:class}`~transactron.lib.basicio.OutputBuffer` component exposes an output instead.
It provides a {py:attr}`~transactron.lib.basicio.OutputBuffer.put` method.

:::{note}
The `synchronize=True` constructor parameter for {py:class}`~transactron.lib.basicio.InputSampler` and {py:class}`~transactron.lib.basicio.OutputBuffer` is used because FPGA dev board I/O is not synchronous to the global clock.
It is not needed for synchronous signals.
:::

Transactron methods can only be called from within transaction (or method) definitions.
Here, we define a simple transaction by constructing a {py:class}`~transactron.core.transaction.Transaction` object and immediately calling {py:meth}`~transactron.core.transaction.Transaction.body`.
Inside the body, the {py:attr}`~transactron.lib.basicio.OutputBuffer.put` method is called to set the LED value to the one received from the switch using the {py:attr}`~transactron.lib.basicio.InputSampler.get` method.
Because of the layout definition `layout`, both the `put` parameter and the field of the structure returned from `get` are named `val`.
The transaction defined here will run in every cycle, ensuring that the LED always shows the value of the switch.

:::{note}
Other than method calls, transaction and method bodies can contain arbitrary Python and Amaranth code.
The Python code of the definition is run once, while Amaranth [assignments]{inv:#lang-assigns} are active only in cycles when the defined transaction or method runs.
This will be showcased in later part of the tutorial.
:::

The example component can be synthesized and programmed to your FPGA dev board using the following code.
The code uses the Digilent Arty A7 board.
For it to work, Vivado and `xc3sprog` need to be installed.
To use it with a different dev board, an appropriate platform needs to be imported instead and the correct toolchain for the board needs to be installed.
```python
from transactron import TransactionModule
from amaranth_boards.arty_a7 import ArtyA7_35Platform

ArtyA7_35Platform().build(TransactionModule(LedControl()), do_program=True)
```
Please notice {py:class}`~transactron.core.manager.TransactionModule`, which is used to wrap the `LedControl` elaboratable.
It provides the context required by Transactron code, including the transaction manager.
Without the wrapper, synthesis will fail.
Typically, there should be only one {py:class}`~transactron.core.manager.TransactionModule` for the entire project.

## Method readiness

For now, our example is not very interesting.
We will now spice it up a little by adding triggers to our input and output.
The input of {py:class}`~transactron.lib.basicio.InputSampler` can be sampled only in cycles when the trigger is active; same with setting the output of {py:class}`~transactron.lib.basicio.OutputBuffer`.
The triggers will be controlled by board buttons.

```{literalinclude} _code/ledcontrol2.py
```

:::{warning}
The code assumes that the buttons on the FPGA dev board are active high (pulled down), as is the case on the Arty A7 board.
If the buttons on your dev board are active low (pulled up), change the `polarity` arguments to `False`.
:::

Please notice that flipping the switch now does not result in changes of the LED state unless the trigger buttons are both pressed.
This is because the transaction body now does not run in every cycle.
Instead it runs only in the cycles when both called methods ({py:attr}`~transactron.lib.basicio.InputSampler.get` and {py:attr}`~transactron.lib.basicio.OutputBuffer.put`) are ready, which is controlled by respective trigger buttons `btn_switch` and `btn_led`.

Also notice that the transaction definition did not need to be changed for this change in behavior.
This is because, for a transaction to run in a given clock cycle, every method called by the transaction must be ready in that cycle.
This condition is implicit in transaction definitions.
It allows to safely change prerequisite conditions for calling methods without modifying the caller code.

% TODO: discuss edge triggering later, when introducing FIFOs
%
%Try flipping the switch when both buttons are pressed.
%You will see that the LED state is immediately updated.
%For the state to change only in the instant one of the buttons is pressed, an `edge=True` parameter should be added to the constructor of {py:class}`~transactron.lib.basicio.InputSampler` (or {py:class}`~transactron.lib.basicio.OutputBuffer`).
%This will make the first (or second) button to be edge sensitive instead of level sensitive.
%
%:::{attention}
%If both buttons are made edge sensitive, it will be basically impossible to update the LED state.
%This is because the buttons would need to be pushed in the exactly the same clock cycle, which is very improbable with the default clock on FPGA dev boards being in the MHz range.
%:::

## Transaction conflicts

In the following example we have two switches controlling the state of a single LED.
Each of the switches has its own trigger button, but the LED output is always triggered.

```{literalinclude} _code/ledcontrol3.py
```

Pressing the first button changes the state of the LED to the state of the first switch.
The same thing happens with the second button and the second switch.
Now try pressing both buttons at once.
The state of the LED should now show the state of one of the switches, and the other one should be ignored.

What happens is that we now have two different transactions, both trying to call `led_buffer.put`.
Method calls are exclusive: in each clock cycle, at most one running transaction can call a given method.
When only one of the buttons is pressed, only one of the `switchN_sampler.get` methods is ready, and the transaction that calls the ready method is run.
But when both buttons are pressed, both transactions are runnable, but they can't both run in the same clock cycle because of the `led_buffer.put` call.
A situation like this is called a transaction conflict.

Transactron automatically ensures that conflicting transactions are never run in the same clock cycle.
Resolving transaction conflicts is performed by an arbitration circuit generated by the transaction manager.
This circuit is an implicit part of every project using Transactron.
While usually invisible, it introduces combinational paths between circuits connected with Transactron, which sometimes can affect timing, or even, in worst cases, introduce a combinational loop.

## Transaction priorities

Transactron does not specify which of the conflicting transactions will be run.
However, it is possible to influence the choice using priority declarations.
To write a priority declaration, the {py:class}`~transactron.core.transaction.Transaction` objects must first be assigned to local variables, for example like this:

```python
        with (t1 := Transaction()).body(m):
            led_buffer.put(m, val=switch1_sampler.get(m).val)

        with (t2 := Transaction()).body(m):
            led_buffer.put(m, val=switch2_sampler.get(m).val)
```

One then calls {py:meth}`~transactron.core.transaction_base.TransactionBase.schedule_before` as follows:

```python
        t1.schedule_before(t2)
```

The priority declaration means that, if both transactions `t1` and `t2` are runnable in the given clock cycle, the transaction manager will consider `t1` for running before `t2`.

:::{warning}
This does not guarantee that `t1` will be selected over `t2` in every possible situation!
Suppose that there is a third transaction `t3` which conflicts with `t1`, but not `t2`.
If the transaction manager runs `t3`, then it is not possible to run `t1` because of the conflict, but `t2` is still runnable.
Therefore `t2` can run together with `t3`, even though `t1` is runnable.
Using priority declarations for specifying behavior is therefore **unreliable** and **discouraged**.
:::

What are priority declarations good for?
A typical use case is preventing a combinational loop through the transaction manager.

% TODO: example
