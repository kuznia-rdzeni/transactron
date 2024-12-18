from collections import defaultdict, deque
from collections.abc import Callable, Iterable, Sequence, Collection, Mapping
from typing import TypeAlias, Optional
from os import environ
from graphlib import TopologicalSorter
from amaranth import *
from amaranth.lib.wiring import Component, connect, flipped
from itertools import chain, filterfalse, product

from amaranth_types import AbstractComponent

from transactron.utils import *
from transactron.utils.transactron_helpers import _graph_ccs
from transactron.graph import OwnershipGraph, Direction

from .transaction_base import Priority, Relation, RelationBase
from .body import Body, TBody, MBody
from .transaction import Transaction, TransactionManagerKey
from .method import Method
from .tmodule import TModule
from .schedulers import eager_deterministic_cc_scheduler

__all__ = ["TransactionManager", "TransactionModule", "TransactionComponent"]

TransactionGraph: TypeAlias = Graph[TBody]
TransactionGraphCC: TypeAlias = GraphCC[TBody]
PriorityOrder: TypeAlias = dict[TBody, int]
TransactionScheduler: TypeAlias = Callable[["MethodMap", TransactionGraph, TransactionGraphCC, PriorityOrder], Module]


class MethodMap:
    def __init__(self, transactions: Iterable[Transaction]):
        self.methods_by_transaction = dict[TBody, list[MBody]]()
        self.transactions_by_method = defaultdict[MBody, list[TBody]](list)
        self.argument_by_call = dict[tuple[TBody, MBody], MethodStruct]()
        self.ancestors_by_call = dict[tuple[TBody, MBody], tuple[MBody, ...]]()
        self.method_parents = defaultdict[MBody, list[Body]](list)

        def rec(transaction: TBody, source: Body, ancestors: tuple[MBody, ...]):
            for method_obj, (arg_rec, _) in source.method_uses.items():
                method = MBody(method_obj._body)
                if method in self.methods_by_transaction[transaction]:
                    raise RuntimeError(f"Method '{method_obj.name}' can't be called twice from the same transaction")
                self.methods_by_transaction[transaction].append(method)
                self.transactions_by_method[method].append(transaction)
                self.argument_by_call[(transaction, method)] = arg_rec
                self.ancestors_by_call[(transaction, method)] = new_ancestors = (method, *ancestors)
                rec(transaction, method, new_ancestors)

        for transaction in transactions:
            self.methods_by_transaction[TBody(transaction._body)] = []
            rec(TBody(transaction._body), transaction._body, ())

        for transaction_or_method in self.methods_and_transactions:
            for method in transaction_or_method.method_uses.keys():
                self.method_parents[MBody(method._body)].append(transaction_or_method)

    def transactions_for(self, elem: Body) -> Collection[TBody]:
        if elem in self.transactions_by_method:
            return self.transactions_by_method[elem]
        else:
            assert elem in self.methods_by_transaction
            return [TBody(elem)]

    @property
    def methods(self) -> Collection[MBody]:
        return self.transactions_by_method.keys()

    @property
    def transactions(self) -> Collection[TBody]:
        return self.methods_by_transaction.keys()

    @property
    def methods_and_transactions(self) -> Iterable[Body]:
        return chain(self.methods, self.transactions)


class TransactionManager(Elaboratable):
    """Transaction manager

    This module is responsible for granting `Transaction`\\s and running
    `Method`\\s. It takes care that two conflicting `Transaction`\\s
    are never granted in the same clock cycle.
    """

    def __init__(self, cc_scheduler: TransactionScheduler = eager_deterministic_cc_scheduler):
        self.transactions: list[Transaction] = []
        self.methods: list[Method] = []
        self.cc_scheduler = cc_scheduler

    def _add_transaction(self, transaction: Transaction):
        self.transactions.append(transaction)

    def _add_method(self, method: Method):
        self.methods.append(method)

    @staticmethod
    def _conflict_graph(method_map: MethodMap) -> tuple[TransactionGraph, PriorityOrder]:
        """_conflict_graph

        This function generates the graph of transaction conflicts. Conflicts
        between transactions can be explicit or implicit. Two transactions
        conflict explicitly, if a conflict was added between the transactions
        or the methods used by them via `add_conflict`. Two transactions
        conflict implicitly if they are both using the same method.

        Created graph is undirected. Transactions are nodes in that graph
        and conflict between two transactions is marked as an edge. In such
        representation connected components are sets of transactions which can
        potentially conflict so there is a need to arbitrate between them.
        On the other hand when two transactions are in different connected
        components, then they can be scheduled independently, because they
        will have no conflicts.

        This function also computes a linear ordering of transactions
        which is consistent with conflict priorities of methods and
        transactions. When priority constraints cannot be satisfied,
        an exception is thrown.

        Returns
        -------
        cgr : TransactionGraph
            Graph of conflicts between transactions, where vertices are transactions and edges are conflicts.
        porder : PriorityOrder
            Linear ordering of transactions which is consistent with priority constraints.
        """

        def transactions_exclusive(trans1: TBody, trans2: TBody):
            tms1 = [trans1] + method_map.methods_by_transaction[trans1]
            tms2 = [trans2] + method_map.methods_by_transaction[trans2]

            # if first transaction is exclusive with the second transaction, or this is true for
            # any called methods, the transactions will never run at the same time
            for tm1, tm2 in product(tms1, tms2):
                if tm1.ctrl_path.exclusive_with(tm2.ctrl_path):
                    return True

            return False

        def calls_nonexclusive(trans1: TBody, trans2: TBody, method: MBody):
            ancestors1 = method_map.ancestors_by_call[(trans1, method)]
            ancestors2 = method_map.ancestors_by_call[(trans2, method)]
            common_ancestors = longest_common_prefix(ancestors1, ancestors2)
            return common_ancestors[-1].nonexclusive

        cgr: TransactionGraph = {}  # Conflict graph
        pgr: TransactionGraph = {}  # Priority graph

        def add_edge(begin: TBody, end: TBody, priority: Priority, conflict: bool):
            if conflict:
                cgr[begin].add(end)
                cgr[end].add(begin)
            match priority:
                case Priority.LEFT:
                    pgr[end].add(begin)
                case Priority.RIGHT:
                    pgr[begin].add(end)

        for transaction in method_map.transactions:
            cgr[transaction] = set()
            pgr[transaction] = set()

        for method in method_map.methods:
            for transaction1 in method_map.transactions_for(method):
                for transaction2 in method_map.transactions_for(method):
                    if (
                        transaction1 is not transaction2
                        and not transactions_exclusive(transaction1, transaction2)
                        and not calls_nonexclusive(transaction1, transaction2, method)
                    ):
                        add_edge(transaction1, transaction2, Priority.UNDEFINED, True)

        relations = [
            Relation(start=elem, **dataclass_asdict(relation))
            for elem in method_map.methods_and_transactions
            for relation in elem.relations
        ]

        for relation in relations:
            start = relation.start
            end = relation.end
            if not relation.conflict:  # relation added with schedule_before
                if end.def_order < start.def_order and not relation.silence_warning:
                    raise RuntimeError(f"{start.name!r} scheduled before {end.name!r}, but defined afterwards")

            for trans_start in method_map.transactions_for(start):
                for trans_end in method_map.transactions_for(end):
                    conflict = relation.conflict and not transactions_exclusive(trans_start, trans_end)
                    add_edge(trans_start, trans_end, relation.priority, conflict)

        porder: PriorityOrder = {}

        for k, transaction in enumerate(TopologicalSorter(pgr).static_order()):
            porder[transaction] = k

        return cgr, porder

    @staticmethod
    def _method_enables(method_map: MethodMap) -> Mapping[TBody, Mapping[MBody, ValueLike]]:
        method_enables = defaultdict[TBody, dict[MBody, ValueLike]](dict)
        enables: list[ValueLike] = []

        def rec(transaction: TBody, source: Body):
            for method, (_, enable) in source.method_uses.items():
                enables.append(enable)
                rec(transaction, method._body)
                method_enables[transaction][MBody(method._body)] = Cat(*enables).all()
                enables.pop()

        for transaction in method_map.transactions:
            rec(transaction, transaction)

        return method_enables

    @staticmethod
    def _method_calls(
        m: Module, method_map: MethodMap
    ) -> tuple[Mapping[MBody, Sequence[MethodStruct]], Mapping[MBody, Sequence[Value]]]:
        args = defaultdict[MBody, list[MethodStruct]](list)
        runs = defaultdict[MBody, list[Value]](list)

        for source in method_map.methods_and_transactions:
            if source in method_map.methods:
                run_val = Cat(transaction.run for transaction in method_map.transactions_by_method[MBody(source)]).any()
                run = Signal()
                m.d.comb += run.eq(run_val)
            else:
                run = source.run
            for method, (arg, _) in source.method_uses.items():
                args[method._body].append(arg)
                runs[method._body].append(run)

        return (args, runs)

    def _simultaneous(self):
        method_map = MethodMap(self.transactions)

        # remove orderings between simultaneous methods/transactions
        # TODO: can it be done after transitivity, possibly catching more cases?
        for elem in method_map.methods_and_transactions:
            all_sims = frozenset(elem.simultaneous_list)
            elem.relations = list(
                filterfalse(
                    lambda relation: not relation.conflict
                    and relation.priority != Priority.UNDEFINED
                    and relation.end in all_sims,
                    elem.relations,
                )
            )

        # step 1: simultaneous and independent sets generation
        independents = defaultdict[TBody, set[TBody]](set)

        for elem in method_map.methods_and_transactions:
            indeps = frozenset[TBody]().union(
                *(frozenset(method_map.transactions_for(ind)) for ind in chain([elem], elem.independent_list))
            )
            for transaction1, transaction2 in product(indeps, indeps):
                independents[transaction1].add(transaction2)

        simultaneous = set[frozenset[TBody]]()

        for elem in method_map.methods_and_transactions:
            for sim_elem in elem.simultaneous_list:
                for tr1, tr2 in product(method_map.transactions_for(elem), method_map.transactions_for(sim_elem)):
                    if tr1 in independents[tr2]:
                        raise RuntimeError(
                            f"Unsatisfiable simultaneity constraints for '{elem.name}' and '{sim_elem.name}'"
                        )
                    simultaneous.add(frozenset({tr1, tr2}))

        # step 2: transitivity computation
        tr_simultaneous = set[frozenset[TBody]]()

        def conflicting(group: frozenset[TBody]):
            return any(tr1 != tr2 and tr1 in independents[tr2] for tr1 in group for tr2 in group)

        q = deque[frozenset[TBody]](simultaneous)

        while q:
            new_group = q.popleft()
            if new_group in tr_simultaneous or conflicting(new_group):
                continue
            q.extend(new_group | other_group for other_group in simultaneous if new_group & other_group)
            tr_simultaneous.add(new_group)

        # step 3: maximal group selection
        def maximal(group: frozenset[TBody]):
            return not any(group.issubset(group2) and group != group2 for group2 in tr_simultaneous)

        final_simultaneous = set(filter(maximal, tr_simultaneous))

        # step 4: convert transactions to methods
        joined_transactions = set[TBody]().union(*final_simultaneous)

        self.transactions = list(filter(lambda t: t._body not in joined_transactions, self.transactions))
        methods = dict[TBody, Method]()

        m = TModule()
        m._MustUse__silence = True  # type: ignore

        for transaction in joined_transactions:
            method = Method(name=transaction.name, src_loc=transaction.src_loc)
            method._set_impl(m, transaction)
            methods[transaction] = method

        # step 5: construct merged transactions
        with DependencyContext(DependencyManager()):
            DependencyContext.get().add_dependency(TransactionManagerKey(), self)
            for group in final_simultaneous:
                name = "_".join([t.name for t in group])
                with Transaction(name=name).body(m):
                    for transaction in group:
                        methods[transaction](m)

        return m

    def elaborate(self, platform):
        for elem in chain(self.transactions, self.methods):
            for relation in elem.relations:
                elem._body.relations.append(RelationBase(**{**dataclass_asdict(relation), "end": relation.end._body}))
            for elem2 in elem.simultaneous_list:
                elem._body.simultaneous_list.append(elem2._body)
            for elem2 in elem.independent_list:
                elem._body.independent_list.append(elem2._body)
            elem.relations = []
            elem.simultaneous_list = []
            elem.independent_list = []

        # In the following, various problems in the transaction set-up are detected.
        # The exception triggers an unused Elaboratable warning.
        with silence_mustuse(self):
            merge_manager = self._simultaneous()

            method_map = MethodMap(self.transactions)
            cgr, porder = TransactionManager._conflict_graph(method_map)

        m = Module()
        m._MustUse__silence = True  # type: ignore
        m.submodules.merge_manager = merge_manager

        for elem in method_map.methods_and_transactions:
            elem._set_method_uses(m)

        for transaction in method_map.transactions:
            ready = [
                method._validate_arguments(method_map.argument_by_call[transaction, method])
                for method in method_map.methods_by_transaction[transaction]
            ]
            m.d.comb += transaction.runnable.eq(Cat(ready).all())

        ccs = _graph_ccs(cgr)

        method_enables = self._method_enables(method_map)

        for method, transactions in method_map.transactions_by_method.items():
            granted = Cat(transaction.run & method_enables[transaction][method] for transaction in transactions)
            m.d.comb += method.run.eq(granted.any())

        (method_args, method_runs) = self._method_calls(m, method_map)

        for method in method_map.methods:
            if len(method_args[method]) == 1:
                m.d.comb += method.data_in.eq(method_args[method][0])
            else:
                if method.single_caller:
                    raise RuntimeError(f"Single-caller method '{method.name}' called more than once")

                runs = Cat(method_runs[method])
                m.d.comb += assign(method.data_in, method.combiner(m, method_args[method], runs), fields=AssignType.ALL)

        m.submodules._transactron_schedulers = ModuleConnector(
            *[self.cc_scheduler(method_map, cgr, cc, porder) for cc in ccs]
        )

        if "TRANSACTRON_VERBOSE" in environ:
            self.print_info(cgr, porder, ccs, method_map)

        return m

    def print_info(
        self, cgr: TransactionGraph, porder: PriorityOrder, ccs: list[GraphCC["TBody"]], method_map: MethodMap
    ):
        print("Transactron statistics")
        print(f"\tMethods: {len(method_map.methods)}")
        print(f"\tTransactions: {len(method_map.transactions)}")
        print(f"\tIndependent subgraphs: {len(ccs)}")
        print(f"\tAvg callers per method: {average_dict_of_lists(method_map.transactions_by_method):.2f}")
        print(f"\tAvg conflicts per transaction: {average_dict_of_lists(cgr):.2f}")
        print("")
        print("Transaction subgraphs")
        for cc in ccs:
            ccl = list(cc)
            ccl.sort(key=lambda t: porder[t])
            for t in ccl:
                print(f"\t{t.name}")
            print("")
        print("Calling transactions per method")
        for m, ts in method_map.transactions_by_method.items():
            print(f"\t{m.owned_name}: {m.src_loc[0]}:{m.src_loc[1]}")
            for t in ts:
                print(f"\t\t{t.name}: {t.src_loc[0]}:{t.src_loc[1]}")
            print("")
        print("Called methods per transaction")
        for t, ms in method_map.methods_by_transaction.items():
            print(f"\t{t.name}: {t.src_loc[0]}:{t.src_loc[1]}")
            for m in ms:
                print(f"\t\t{m.owned_name}: {m.src_loc[0]}:{m.src_loc[1]}")
            print("")

    def visual_graph(self, fragment):
        graph = OwnershipGraph(fragment)
        method_map = MethodMap(self.transactions)
        for method, transactions in method_map.transactions_by_method.items():
            if len(method.data_in.as_value()) > len(method.data_out.as_value()):
                direction = Direction.IN
            elif method.data_in.shape().size < method.data_out.shape().size:
                direction = Direction.OUT
            else:
                direction = Direction.INOUT
            graph.insert_node(method)
            for transaction in transactions:
                graph.insert_node(transaction)
                graph.insert_edge(transaction, method, direction)

        return graph

    def debug_signals(self) -> SignalBundle:
        method_map = MethodMap(self.transactions)
        cgr, _ = TransactionManager._conflict_graph(method_map)

        def transaction_debug(t: TBody):
            return (
                [t.ready, t.run] + [m.ready for m in method_map.methods_by_transaction[t]] + [t2.run for t2 in cgr[t]]
            )

        def method_debug(m: MBody):
            return [m.ready, m.run, {t.name: transaction_debug(t) for t in method_map.transactions_by_method[m]}]

        return {
            "transactions": {t.name: transaction_debug(t) for t in method_map.transactions},
            "methods": {m.owned_name: method_debug(m) for m in method_map.methods},
        }


class TransactionModule(Elaboratable):
    """
    `TransactionModule` is used as wrapper on `Elaboratable` classes,
    which adds support for transactions. It creates a
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

        m = Module()

        m.submodules.main_module = elaboratable
        m.submodules.transactionManager = self.transaction_manager = self.manager.get_dependency(
            TransactionManagerKey()
        )

        return m


class TransactionComponent(TransactionModule, Component):
    """Top-level component for Transactron projects.

    The `TransactronComponent` is a wrapper on `Component` classes,
    which adds Transactron support for the wrapped class. The use
    case is to wrap a top-level module of the project, and pass the
    wrapped module for simulation, HDL generation or synthesis.
    The ports of the wrapped component are forwarded to the wrapper.

    It extends the functionality of `TransactionModule`.
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
        TransactionModule.__init__(self, component, dependency_manager, transaction_manager)
        Component.__init__(self, component.signature)

    def elaborate(self, platform):
        m = super().elaborate(platform)

        assert isinstance(self.elaboratable, Component)  # for typing
        connect(m, flipped(self), self.elaboratable)

        return m
