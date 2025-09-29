from typing import Optional
from amaranth import *
from amaranth.lib.wiring import Component, connect, flipped

from amaranth_types import AbstractComponent, HasElaborate

from .manager import TransactionManager
from .keys import TransactionManagerKey

from transactron.utils import DependencyContext, DependencyManager, silence_mustuse


__all__ = ["TransactronContextElaboratable", "TransactronContextComponent"]


class TransactronContextElaboratable(Elaboratable):
    """Top-level Elaboratable for Transactron projects.

    `TransactronContextElaboratable` is used as wrapper on `Elaboratable`
    classes, which adds support for transactions. It creates a
    `TransactionManager` which will handle transaction scheduling
    and can be used in definition of `Method`\\s and `Transaction`\\s.
    The `TransactionManager` is stored in a `DependencyManager`.
    """

    def __init__(
        self,
        elaboratable: HasElaborate,
        dependency_manager: Optional[DependencyManager] = None,
        transaction_manager: Optional[TransactionManager] = None,
    ):
        """
        Parameters
        ----------
        elaboratable: HasElaborate
            The `Elaboratable` which should be wrapped to add support for
            transactions and methods.
        dependency_manager: DependencyManager, optional
            The `DependencyManager` to use inside the transaction module.
            If omitted, a new one is created.
        transaction_manager: TransactionManager, optional
            The `TransactionManager` to use inside the transaction module.
            If omitted, a new one is created.
        """
        if transaction_manager is None:
            transaction_manager = TransactionManager()
        if dependency_manager is None:
            dependency_manager = DependencyManager()
        self.manager = dependency_manager
        self.manager.add_dependency(TransactionManagerKey(), transaction_manager)
        self.elaboratable = elaboratable

    def context(self) -> DependencyContext:
        return DependencyContext(self.manager)

    def elaborate(self, platform):
        with silence_mustuse(self.manager.get_dependency(TransactionManagerKey())):
            with self.context():
                elaboratable = Fragment.get(self.elaboratable, platform)
                self.transaction_manager = self.manager.get_dependency(TransactionManagerKey())
                elab_transaction_manager = Fragment.get(self.transaction_manager, platform)

        m = Module()

        m.submodules.main_module = elaboratable
        m.submodules.transactionManager = elab_transaction_manager

        return m


class TransactronContextComponent(TransactronContextElaboratable, Component):
    """Top-level component for Transactron projects.

    The `TransactronContextComponent` is a wrapper on `Component` classes,
    which adds Transactron support for the wrapped class. The use
    case is to wrap a top-level module of the project, and pass the
    wrapped module for simulation, HDL generation or synthesis.
    The ports of the wrapped component are forwarded to the wrapper.

    It extends the functionality of `TransactronContextComponent`.
    """

    def __init__(
        self,
        component: AbstractComponent,
        dependency_manager: Optional[DependencyManager] = None,
        transaction_manager: Optional[TransactionManager] = None,
    ):
        """
        Parameters
        ----------
        component: Component
            The `Component` which should be wrapped to add support for
            transactions and methods.
        dependency_manager: DependencyManager, optional
            The `DependencyManager` to use inside the transaction component.
            If omitted, a new one is created.
        transaction_manager: TransactionManager, optional
            The `TransactionManager` to use inside the transaction component.
            If omitted, a new one is created.
        """
        TransactronContextElaboratable.__init__(self, component, dependency_manager, transaction_manager)
        Component.__init__(self, component.signature)

    def elaborate(self, platform):
        m = super().elaborate(platform)

        assert isinstance(self.elaboratable, Component)  # for typing
        connect(m, flipped(self), self.elaboratable)

        return m
