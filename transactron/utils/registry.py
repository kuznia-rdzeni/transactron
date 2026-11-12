from collections.abc import Callable, Iterable, Iterator, Sequence
from typing import Any, overload

__all__ = ["Registry"]


class Registry[T, **P](Sequence[T]):
    @overload
    def __init__[U](self: "Registry[U, []]", constructor: Callable[[], U], *, count: int) -> None: ...

    @overload
    def __init__(self, constructor: Callable[P, T], *, init: Iterable[T] = ()) -> None: ...

    def __init__(self, constructor: Callable[..., T], *, count: int | None = None, init: Iterable[T] | None = None):
        if count is not None and init is not None:
            raise ValueError("Registry: count and init cannot be used together")
        self._constructor = constructor
        self._elems = list(init) if init is not None else []
        if count is not None:
            self._elems.extend(constructor() for _ in range(count))
        self._frozen = False

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> T:
        if self._frozen:
            raise RuntimeError("Registry: cannot add elements after freezing")
        elem = self._constructor(*args, **kwargs)
        self._elems.append(elem)
        return elem

    def freeze(self) -> None:
        self._frozen = True

    @property
    def frozen(self) -> bool:
        return self._frozen

    @overload
    def __getitem__(self, index: int) -> T: ...

    @overload
    def __getitem__(self, index: slice) -> Sequence[T]: ...

    def __getitem__(self, index: Any) -> Any:
        if isinstance(index, slice):
            return tuple(self._elems[index])
        return self._elems[index]

    def __len__(self) -> int:
        return len(self._elems)

    def __iter__(self) -> Iterator[T]:
        return iter(self._elems)
