from collections.abc import Callable

from .log import RawEventSink
from .schema import GeneratedEvLog, SignalHandle


__all__ = ["SignalReader", "HandleResolver", "GeneratedEvLogSampler"]


SignalReader = Callable[[], int]
"""Reads the current value of a single signal in a running (or replayed)
simulation."""

HandleResolver = Callable[[SignalHandle], SignalReader]
"""Resolves the location of a signal in generated Verilog code to a reader
of its value. Resolution happens once per signal, so the resolver can cache
simulator handles."""


class GeneratedEvLogSampler:
    """Simulator-agnostic event sampler for generated (Verilog) designs.

    The sampler reads event signals through readers obtained from a
    `HandleResolver`, so the same code works with any backend able to read
    signals by their hierarchical location.

    `sample` should be called once per clock cycle, at a moment when the
    sampled signals are stable (e.g. on the falling clock edge). If the
    generated design provides a packed trigger vector, only a single signal
    read per cycle is needed to check all emission sites.
    """

    def __init__(self, generated: GeneratedEvLog, resolve: HandleResolver):
        """
        Parameters
        ----------
        generated: GeneratedEvLog
            Event log information from `GenerationInfo`.
        resolve: HandleResolver
            Backend-specific signal location resolver.
        """
        self.generated = generated
        self._packed_triggers = (
            resolve(generated.triggers_location) if generated.triggers_location is not None else None
        )
        self._sites = [
            (resolve(loc.trigger), [resolve(field) for field in loc.fields]) for loc in generated.site_locations
        ]

    def sample(self, cycle: int, sink: RawEventSink) -> None:
        """Samples all emission sites and reports fired events to the sink."""
        if self._packed_triggers is not None:
            packed = self._packed_triggers()
            if not packed:
                return
            for site, (_, field_readers) in enumerate(self._sites):
                if packed >> site & 1:
                    sink.emit_raw(cycle, site, [read() for read in field_readers])
        else:
            for site, (trigger_reader, field_readers) in enumerate(self._sites):
                if trigger_reader():
                    sink.emit_raw(cycle, site, [read() for read in field_readers])
