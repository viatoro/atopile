# This file is part of the faebryk project
# SPDX-License-Identifier: MIT

"""CLI entry point: python -m faebryk.libs.eda

Sub-commands:
    convert <file> [--output] [--force-altium-out]
    info <file>
    netlist <file>
    hl <file>
"""

from __future__ import annotations

from pathlib import Path

import typer

app = typer.Typer(help="EDA file tooling (Altium, KiCad).")

_KICAD_PCB_SUFFIXES = {".kicad_pcb"}
_KICAD_SCH_SUFFIXES = {".kicad_sch"}
_ALTIUM_PCB_SUFFIXES = {".pcbdoc"}
_ALTIUM_SCH_SUFFIXES = {".schdoc"}
_CADENCE_NETLIST_SUFFIXES = {".net", ".cdl", ".netlist"}


def _file_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in _KICAD_PCB_SUFFIXES:
        return "kicad_pcb"
    if suffix in _KICAD_SCH_SUFFIXES:
        return "kicad_sch"
    if suffix in _ALTIUM_PCB_SUFFIXES:
        return "altium_pcb"
    if suffix in _ALTIUM_SCH_SUFFIXES:
        return "altium_sch"
    if suffix in _CADENCE_NETLIST_SUFFIXES:
        return "cadence_netlist"
    return "unknown"


# ---------------------------------------------------------------------------
# convert
# ---------------------------------------------------------------------------


@app.command()
def convert(
    input: Path = typer.Argument(..., help=".kicad_pcb, .PcbDoc, or .SchDoc file"),
    output: Path = typer.Option(None, help="Output path (default: swap extension)"),
    force_altium_out: bool = typer.Option(
        False,
        "--force-altium-out",
        help="Force file->ll->il->ll->file roundtrip to .PcbDoc",
    ),
) -> None:
    """Convert between KiCad and Altium formats."""
    kind = _file_kind(input)

    if force_altium_out and kind == "altium_pcb":
        _altium_pcb_roundtrip(input, output)
    elif kind == "kicad_pcb":
        _kicad_to_altium(input, output)
    elif kind == "altium_pcb":
        _altium_pcb_to_kicad(input, output)
    else:
        typer.echo(f"Unsupported file type for convert: {input.suffix}", err=True)
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# info
# ---------------------------------------------------------------------------


@app.command()
def info(
    input: Path = typer.Argument(
        ..., help=".kicad_pcb, .kicad_sch, .PcbDoc, or .SchDoc file"
    ),
) -> None:
    """Display summary information about a PCB or schematic file."""
    kind = _file_kind(input)

    if kind in ("altium_pcb", "kicad_pcb"):
        _info_pcb(input)
    elif kind in ("altium_sch", "kicad_sch"):
        _info_schematic(input)
    else:
        typer.echo(f"Unsupported file type for info: {input.suffix}", err=True)
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# hl
# ---------------------------------------------------------------------------


@app.command()
def hl(
    input: Path = typer.Argument(
        ..., help=".kicad_pcb, .kicad_sch, .PcbDoc, .SchDoc, or .net file"
    ),
) -> None:
    """Print the HL model for a PCB, schematic, or netlist file."""
    import dataclasses

    from faebryk.libs.eda.hl.convert.eda_file_hl import PCB, Netlist, Schematic
    from faebryk.libs.util import indented_container

    kind = _file_kind(input)

    try:
        if kind in ("altium_pcb", "kicad_pcb"):
            model = PCB.decode(input)
        elif kind in ("altium_sch", "kicad_sch"):
            model = Schematic.decode(input)
        elif kind == "cadence_netlist":
            model = Netlist.decode(input)
        else:
            typer.echo(f"Unsupported file type for hl: {input.suffix}", err=True)
            raise typer.Exit(1)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    typer.echo(indented_container(dataclasses.asdict(model), recursive=True))


# ---------------------------------------------------------------------------
# netlist
# ---------------------------------------------------------------------------


@app.command()
def netlist(
    input: Path = typer.Argument(
        ..., help=".kicad_pcb, .kicad_sch, .PcbDoc, or .SchDoc file"
    ),
) -> None:
    """Display a connectivity-oriented netlist overview."""
    kind = _file_kind(input)

    try:
        netlist_model = _netlist_for_input(input)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    typer.echo(_format_netlist_overview(input, kind, netlist_model))


def _info_pcb(input: Path) -> None:
    from faebryk.libs.eda.hl.convert.eda_file_hl import PCB

    hl = PCB.decode(input)

    def _count_geometries(col) -> int:
        total = len(col.geometries)
        for child in col.collections:
            total += _count_geometries(child)
        return total

    def _collect_nets(col) -> set[str]:
        nets = {g.net.name for g in col.geometries if g.net is not None}
        for child in col.collections:
            nets |= _collect_nets(child)
        return nets

    components = [c for c in hl.collections if c.extra_properties.get("refdes")]

    typer.echo(f"File:        {input}")
    typer.echo(f"Components:  {len(components)}")
    typer.echo(f"Geometries:  {_count_geometries(hl)}")
    typer.echo(f"Nets:        {len(_collect_nets(hl))}")

    if components:
        typer.echo("Components:")
        for col in components:
            refdes = col.extra_properties.get("refdes", "?")
            name = col.extra_properties.get("name", "")
            geom_count = _count_geometries(col)
            typer.echo(f"  {refdes:12s} {name}  ({geom_count} geometries)")


def _info_schematic(input: Path) -> None:
    from faebryk.libs.eda.hl.convert.eda_file_hl import Schematic

    hl = Schematic.decode(input)

    total_symbols = sum(len(s.symbols) for s in hl.sheets)
    total_pins = sum(len(sym.pins) for s in hl.sheets for sym in s.symbols)
    total_wires = sum(len(s.wires) for s in hl.sheets)
    total_nets = sum(len(s.nets) for s in hl.sheets)
    total_junctions = sum(len(s.junctions) for s in hl.sheets)

    typer.echo(f"File:       {input}")
    typer.echo(f"Sheets:     {len(hl.sheets)}")
    typer.echo(f"Symbols:    {total_symbols}")
    typer.echo(f"Pins:       {total_pins}")
    typer.echo(f"Wires:      {total_wires}")
    typer.echo(f"Nets:       {total_nets}")
    typer.echo(f"Junctions:  {total_junctions}")

    for sheet in hl.sheets:
        sheet_name = sheet.name or sheet.id or "?"
        typer.echo(f"Sheet: {sheet_name}")
        for sym in sheet.symbols:
            refdes = sym.refdes or "?"
            name = sym.name or ""
            typer.echo(f"  {refdes:12s} {name}  ({len(sym.pins)} pins)")


def _netlist_for_input(input: Path):
    from faebryk.libs.eda.hl.convert.eda_file_hl import PCB, Netlist, Schematic
    from faebryk.libs.eda.hl.convert.pcb_netlist import convert_pcb_to_netlist
    from faebryk.libs.eda.hl.convert.schematic_netlist import (
        convert_schematic_to_netlist,
    )

    kind = _file_kind(input)
    if kind in ("altium_pcb", "kicad_pcb"):
        return convert_pcb_to_netlist(PCB.decode(input))
    if kind in ("altium_sch", "kicad_sch"):
        return convert_schematic_to_netlist(Schematic.decode(input))
    if kind == "cadence_netlist":
        return Netlist.decode(input)

    expected = ", ".join(
        sorted(
            _ALTIUM_PCB_SUFFIXES
            | _KICAD_PCB_SUFFIXES
            | _ALTIUM_SCH_SUFFIXES
            | _KICAD_SCH_SUFFIXES
            | _CADENCE_NETLIST_SUFFIXES
        )
    )
    raise ValueError(
        f"Unsupported file type for netlist: {input.suffix} (expected {expected})"
    )


def _net_display_name(net) -> str:
    name = net.name or net.id
    if not net.aliases:
        return name
    return f"{name} [{', '.join(net.aliases)}]"


def _terminal_display_name(terminal) -> str:
    owner = terminal.owner_name or terminal.owner_id
    if owner and terminal.terminal_id:
        return f"{owner}.{terminal.terminal_id}"
    if terminal.terminal_id:
        return terminal.terminal_id
    return owner or "?"


def _format_netlist_overview(input: Path, kind: str, netlist_model) -> str:
    nets = sorted(netlist_model.nets, key=lambda net: (net.name or net.id, net.id))
    lines = [
        f"File:  {input}",
        f"Kind:  {kind}",
        f"Nets:  {len(nets)}",
    ]

    if not nets:
        lines.extend(["", "(no nets found)"])
        return "\n".join(lines)

    for net in nets:
        terminals = sorted(net.terminals, key=lambda terminal: terminal.normalized())
        lines.extend(
            [
                "",
                f"{_net_display_name(net)} ({len(terminals)} terminals)",
            ]
        )
        for terminal in terminals:
            lines.append(f"  {terminal.kind:14s} {_terminal_display_name(terminal)}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# convert helpers
# ---------------------------------------------------------------------------


def _kicad_to_altium(input: Path, output: Path | None) -> None:
    from faebryk.libs.eda.altium import export_altium_pcb
    from faebryk.libs.kicad.fileformats import kicad

    if output is None:
        output = input.with_suffix(".PcbDoc")

    pcb_file = kicad.loads(kicad.pcb.PcbFile, input)
    export_altium_pcb(pcb_file.kicad_pcb, output)
    typer.echo(f"{input} -> {output}")


def _altium_pcb_to_kicad(input: Path, output: Path | None) -> None:
    from faebryk.libs.eda.altium.convert.pcb.file_ll import PcbDocCodec
    from faebryk.libs.eda.altium.convert.pcb.il_kicad import convert_altium_to_kicad
    from faebryk.libs.eda.altium.convert.pcb.il_ll import convert_ll_to_il
    from faebryk.libs.kicad.fileformats import kicad

    if output is None:
        output = input.with_suffix(".kicad_pcb")

    ll_doc = PcbDocCodec.read(input)
    il_doc = convert_ll_to_il(ll_doc)
    kicad_pcb = convert_altium_to_kicad(il_doc)
    pcb_file = kicad.pcb.PcbFile(kicad_pcb=kicad_pcb)
    kicad.dumps(pcb_file, output)
    typer.echo(f"{input} -> {output}")


def _altium_pcb_roundtrip(input: Path, output: Path | None) -> None:
    from faebryk.libs.eda.altium.convert.pcb.file_ll import PcbDocCodec
    from faebryk.libs.eda.altium.convert.pcb.il_ll import (
        convert_il_to_ll,
        convert_ll_to_il,
    )

    if output is None:
        output = input.with_suffix(".out.PcbDoc")

    ll_doc = PcbDocCodec.read(input)
    il_doc = convert_ll_to_il(ll_doc)
    ll_doc = convert_il_to_ll(il_doc)
    PcbDocCodec.write(ll_doc, output)
    typer.echo(f"{input} -> {output}")


if __name__ == "__main__":
    app()
