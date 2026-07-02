import argparse
import enum
import importlib
import json
import re
import signal
import sys
from typing import Any, Optional

from transactron.evlog.event import get_event_class
from transactron.evlog.log import EventLog
from transactron.evlog.schema import EventSiteSchema


def site_field_types(site: EventSiteSchema) -> dict[str, Any]:
    """Returns the declared field types of the site's event, or an empty
    mapping if the event type is not registered (its defining module was
    not imported)."""
    try:
        return dict(get_event_class(site.event_name)._field_types)
    except KeyError:
        return {}


def format_value(raw: int, width: int, field_type: Any, mode: str) -> str:
    if isinstance(field_type, type) and issubclass(field_type, enum.Enum):
        try:
            return field_type(raw).name
        except ValueError:
            pass
    if mode == "hex" or (mode == "auto" and width > 8):
        return f"0x{raw:0{(width + 3) // 4}x}"
    return str(raw)


def format_static(value: Any, field_type: Any) -> str:
    if isinstance(field_type, type) and issubclass(field_type, enum.Enum) and not isinstance(value, enum.Enum):
        try:
            return field_type(value).name
        except ValueError:
            pass
    return str(value)


def format_event(site: EventSiteSchema, values: list[int], mode: str) -> str:
    field_types = site_field_types(site)
    parts = [
        f"{field.name}={format_value(value, field.width, field_types.get(field.name), mode)}"
        for field, value in zip(site.fields, values)
    ]
    parts += [f"{name}={format_static(value, field_types.get(name))}" for name, value in site.statics.items()]
    return " ".join(parts)


def parse_cycle_range(spec: str) -> tuple[int, int]:
    start, sep, end = spec.partition(":")
    if not sep:
        raise ValueError("Cycle range must have the START:END format")
    return (int(start) if start else 0, int(end) if end else sys.maxsize)


def print_schema(log: EventLog, out):
    if log.schema.metadata:
        print(f"metadata: {json.dumps(log.schema.metadata)}", file=out)
    for idx, site in enumerate(log.schema.sites):
        fields = ", ".join(f"{field.name}[{field.width}]" for field in site.fields)
        statics = "".join(f" {name}={value}" for name, value in site.statics.items())
        location = f"{site.location[0]}:{site.location[1]}"
        print(f"{idx:>4} {site.source_name} {site.event_name} ({fields}){statics}  [{location}]", file=out)


def print_events(log: EventLog, out, *, mode: str, name_filter: Optional[str], cycles: Optional[str]):
    sites = log.schema.sites

    selected = set(range(len(sites)))
    if name_filter is not None:
        pattern = re.compile(name_filter)
        selected = {idx for idx in selected if pattern.search(f"{sites[idx].source_name} {sites[idx].event_name}")}

    first_cycle, last_cycle = parse_cycle_range(cycles) if cycles is not None else (0, sys.maxsize)

    records = [
        (cycle, site, values)
        for cycle, site, values in log.raw
        if site in selected and first_cycle <= cycle <= last_cycle
    ]
    records.sort(key=lambda record: record[0])

    if log.schema.metadata:
        print(f"# metadata: {json.dumps(log.schema.metadata)}", file=out)
    if not records:
        return

    cycle_width = len(str(records[-1][0]))
    source_width = max(len(sites[site].source_name) for _, site, _ in records)
    event_width = max(len(sites[site].event_name) for _, site, _ in records)

    for cycle, site_idx, values in records:
        site = sites[site_idx]
        print(
            f"{cycle:>{cycle_width}} {site.source_name:<{source_width}} "
            f"{site.event_name:<{event_width}}  {format_event(site, values, mode)}",
            file=out,
        )


def main(argv: Optional[list[str]] = None):
    # Die silently when the output is piped to e.g. `head`.
    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)

    parser = argparse.ArgumentParser(description="Pretty-print evlog event log files.")
    parser.add_argument("path", help="The event log file (JSON lines)")
    parser.add_argument(
        "-m",
        "--module",
        action="append",
        default=[],
        help="Module with event definitions to import (e.g. coreblocks.telemetry); can be repeated. "
        "Without it, events are still printed, but enum fields are not converted to member names.",
    )
    parser.add_argument("-f", "--filter", help="Regex selecting events by their 'source event' name")
    parser.add_argument("-c", "--cycles", help="Only show events in the START:END cycle range (bounds optional)")
    value_format = parser.add_mutually_exclusive_group()
    value_format.add_argument(
        "-x",
        "--hex",
        action="store_true",
        help="Print all field values in hex (default: only fields wider than 8 bits)",
    )
    value_format.add_argument("-d", "--dec", action="store_true", help="Print all field values in decimal")
    parser.add_argument("--schema", action="store_true", help="Print the schema summary instead of the events")
    args = parser.parse_args(argv)

    for module in args.module:
        importlib.import_module(module)

    log = EventLog.load(args.path)

    if args.schema:
        print_schema(log, sys.stdout)
        return

    mode = "hex" if args.hex else "dec" if args.dec else "auto"
    print_events(log, sys.stdout, mode=mode, name_filter=args.filter, cycles=args.cycles)


if __name__ == "__main__":
    main()
