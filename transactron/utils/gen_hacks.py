from amaranth.hdl._ir import Design
import amaranth.hdl._mem as _mem


def fixup_vivado_transparent_memories(design: Design):
    # See https://github.com/YosysHQ/yosys/issues/5082
    # Vivado stopped inferring transparent memory ports emitted with Yosys verilog backend
    # correctly from (probably) version 2023.1, printing [Synth 8-6430] warning.
    # It is a Vivado bug, generating circuit behaviour that doesn't match the RTL.
    # It is fixed by adding vivado-specific RTL attribute to main memory declarations that
    # use this pattern.
    # Adds the attribute to all memories with enabled port transparency, needed until (and if)
    # Yosys changes the generated pattern.

    for fragment in design.fragments:  # type: ignore
        if isinstance(fragment, _mem.MemoryInstance):
            is_transparent = any(read_port._transparent_for for read_port in fragment._read_ports)  # type: ignore

            if is_transparent:
                fragment._attrs.setdefault("rw_addr_collision", "yes")  # type: ignore
