"""Cadence LL → HL netlist converter."""

from __future__ import annotations

from collections import defaultdict

from faebryk.libs.eda.cadence.models.netlist import ll as cadence_netlist_ll
from faebryk.libs.eda.hl.models.netlist import Net, Netlist, TerminalRef


def convert_ll_to_hl(ll_netlist: cadence_netlist_ll.Netlist) -> Netlist:
    terminals_by_net: dict[str, list[TerminalRef]] = defaultdict(list)
    for component in ll_netlist.components:
        for ll_pin in component.pins:
            terminals_by_net[ll_pin.net_name].append(
                TerminalRef(
                    kind="netlist_pin",
                    owner_id=component.record_id,
                    owner_name=component.refdes,
                    terminal_id=ll_pin.pin,
                )
            )

    nets: list[Net] = []
    for index, (net_name, terminals) in enumerate(sorted(terminals_by_net.items())):
        nets.append(
            Net(
                id=net_name or f"net-{index + 1}",
                name=net_name or None,
                terminals=sorted(terminals),
            )
        )
    return Netlist(nets=nets)
