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
from transactron import TransactronContextElaboratable
from amaranth_boards.arty_a7 import ArtyA7_35Platform

ArtyA7_35Platform().build(TransactronContextElaboratable(LedControl()), do_program=True)
```
Please notice {py:class}`~transactron.core.context.TransactronContextElaboratable`, which is used to wrap the `LedControl` elaboratable.
It provides the context required by Transactron code, including the transaction manager.
Without the wrapper, synthesis will fail.
Typically, there should be only one {py:class}`~transactron.core.context.TransactronContextElaboratable` for the entire project.

## Method readiness

For now, our example is not very interesting.
We will now spice it up a little by adding triggers to our input and output.
The input of {py:class}`~transactron.lib.basicio.InputSampler` can be sampled only in cycles when the trigger is active; same with setting the output of {py:class}`~transactron.lib.basicio.OutputBuffer`.
The triggers will be controlled by board buttons.

```{literalinclude} _code/ledcontrol2.py
:name: ledcontrol2
```

:::{warning}
The code assumes that the buttons on the FPGA dev board are active high (pulled down), as is the case on the Arty A7 board.
If the buttons on your dev board are active low (pulled up), change the `polarity` parameters to `False`.
:::

Please notice that flipping the switch now does not result in changes of the LED state unless the trigger buttons are both pressed.
This is because the transaction body now does not run in every cycle.
Instead it runs only in the cycles when both called methods ({py:attr}`~transactron.lib.basicio.InputSampler.get` and {py:attr}`~transactron.lib.basicio.OutputBuffer.put`) are ready, which is controlled by respective trigger buttons `btn_switch` and `btn_led`.

Also notice that the transaction definition did not need to be changed for this change in behavior.
This is because, for a transaction to run in a given clock cycle, every method called by the transaction must be ready in that cycle.
This condition is implicit in transaction definitions.
It allows to safely change prerequisite conditions for calling methods without modifying the caller code.

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

## Connecting transactions as submodules

Did you notice that the two transactions in the previous example are almost identical?
Both of them call some method (the `get` method of a sampler) and pass the result immediately to another method (the `put` method of a buffer).
This pattern occurs so often in Transactron that there is a library component for it, {py:class}`~transactron.lib.connectors.ConnectTrans`.
Import it:

```python
from transactron.lib.connectors import ConnectTrans
```

Now replace the two transaction definitions with:

```python
        m.submodules += ConnectTrans.create(led_buffer.put, switch1_sampler.get)
        m.submodules += ConnectTrans.create(led_buffer.put, switch2_sampler.get)
```

The synthesized circuit should work exactly like before.

Notice that we didn't use `val` to reference the parameter of `put` or the field of the result of `get`.
This is because {py:class}`~transactron.lib.connectors.ConnectTrans` works at the level of structures, not individual fields.
The connection requires that the output layout of `get` and the input layout of `put` are both the same layout.

:::{note}
In `ConnectTrans.create(method1, method2)`, the output of `method2` is connected to the input of `method1`.
But at the same time, the output of `method1` is also connected to the input of `method1`: the connection is bidirectional.
In this example, both the input layout of `get` and the output layout of `put` is empty, so everything works as expected.

As a consequence, the method arguments of {py:meth}`~transactron.lib.connectors.ConnectTrans.create` can be swapped without changing the resulting behavior.
:::

## Data structures

Try flipping a switch when the corresponding button is pressed.
You will see that the LED state is immediately updated.
For the state to change only in the instant one of the buttons is pressed, an `edge=True` parameter should be added to the constructor of {py:class}`~transactron.lib.basicio.InputSampler`.
This will make the button sampler to be edge sensitive instead of level sensitive.
The connecting transactions will therefore run only for a single cycle after the button is pressed, rather than continuously.

With that possibility, let's now revisit [the example with a single switch](#ledcontrol2), but spice it up even further by adding a data structure between the switch sampler and the LED buffer: a FIFO queue.

```{literalinclude} _code/ledcontrol4.py
```

The FIFO component {py:class}`~transactron.lib.fifo.BasicFifo` provides, among others, the two methods {py:attr}`~transactron.lib.fifo.BasicFifo.write` and {py:attr}`~transactron.lib.fifo.BasicFifo.read`.
The `write` method inserts new data to the back of the queue, while `read` returns data at the front of the queue and removes it.
Both of these methods are ready only when the respective actions can be correctly performed: `write` requires the queue not to be full, while `read` requires it to be nonempty.
This way, Transactron automatically provides backpressure, which can help prevent overflow and underflow.

To illustrate this, the readiness signals of `write` and `read` are connected to LEDs number 1 and 2.
At the beginning only the `write` method is ready.
Pressing the first button runs the `write` method, which inserts the current value of the switch into the FIFO and makes the `read` method ready.
After a few more presses of the first button the FIFO gets filled up, after which the `write` method is no longer ready.
Further presses of the first button will now have no effect.

Pressing the second button will remove a value from the FIFO and display it on the first LED.
The second button can be pressed until the FIFO is emptied.
Pressing it again will not alter the state of the first LED.
Try playing with the button and the switch some more.

Try swapping the {py:class}`~transactron.lib.fifo.BasicFifo` for a {py:class}`~transactron.lib.stack.Stack`.
For that, only the import and class name need to be changed.

## RPN calculator

We will now implement a larger example: a reverse Polish notation (RPN) calculator.
In this notation, operations are entered postfix, and parentheses are not needed: the expression (2 + 3) * 4 becomes 2 3 + 4 * in RPN.
The algorithm for computing the value of RPN expressions reads symbols (numbers and operators) from left to right and uses a stack to store intermediate results.
When a number is read, it is pushed to the stack.
Reading an operator causes two numbers to be popped from the stack, and the result to be pushed back.

Hardware which implements a RPN calculator needs to be able to perform these two kinds of operations.
The stack data structure in Transactron standard library, {py:class}`~transactron.lib.stack.Stack`, can perform at most one push and one pop per clock cycle.
So, if used directly, performing a RPN operation would require more than one clock cycle.
Instead, we will create a specialized stack structure, which will store the top value of the stack in a register so that a single clock cycle will suffice.

```{literalinclude} _code/rpnstack.py
```

In the constructor we declare methods provided by our component.
The `peek` and `peek2` methods will return the top of the stack and the element immediately below it, both methods will not take any parameters.
The `push` method will insert a new element to the stack, while `pop_set_top` will remove one element and change the value of the one below it.

The methods are defined inside `elaborate` using the {py:class}`~transactron.lib.stack.Stack` component and two additional registers, `top` and `nonempty`.
Methods are defined using {py:class}`~transactron.core.sugar.def_method` decorator syntax.
The method definition is written using Python `def` function syntax.
It works much like the `body` context manager used for defining transactions -- the Python code inside the definition is evaluated exactly once.
Method inputs are passed as parameters, while the result is provided using `return` as a `dict`.

The first method, `peek`, returns the value at the top of the stack, which is stored in the register `top`.
It is ready only when the stack is not empty.
The `nonexclusive=True` parameter to `def_method` allows this method to be called by multiple transactions in a single clock cycle.
This is justified by the fact that `peek` does not alter the state of the component in any way.

TODO: to be continued...

