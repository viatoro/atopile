# atopile hl connectivity prototype

## Goal

Create a minimal high-level layer that is rich enough to reconstruct a netlist
from either a schematic or a PCB import path.

The immediate metric is:

- project a schematic HL model to a netlist through `hl/convert/schematic`
- project a PCB HL model to a netlist through `hl/convert/pcb`
- compare those normalized netlists

## Design choice

This HL layer is not a file format layer. It is not tied to Altium or KiCad.
It is a connectivity-focused abstraction that intentionally ignores rendering,
style, and most layout metadata.

## Scope

- shared `Netlist`, `Net`, and `TerminalRef`
- schematic HL with enough primitives to resolve electrical connectivity,
  including hierarchical sheet interfaces
- PCB HL with recursive collections plus conductive geometry, rich enough to
  recover terminals and connectivity from geometry membership

## Deliberately out of scope for the prototype

- full graphical fidelity
- schematic annotation/layout features unrelated to connectivity
- PCB manufacturing and geometry detail unrelated to the pad-net relation
- multi-sheet hierarchical scoping rules beyond simple global labels/power names
- full PCB geometry solving beyond the current circle/segment/polygon subset
