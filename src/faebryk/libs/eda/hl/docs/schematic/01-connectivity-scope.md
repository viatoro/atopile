# Schematic HL Scope

The prototype schematic HL keeps only these primary objects:

- `Schematic`
- `Sheet`
- `Symbol`
- `Pin`
- `Wire`
- `Junction`
- `Net`

Hierarchy is represented without separate `sheet_port` or `sheet_entry` objects:

- a hierarchical sheet instance is a `Symbol(kind="sheet")`
- the symbol's `Pin`s are the parent-sheet interface points
- the child `Sheet` has matching `Pin`s of its own
- connectivity flows by matching parent and child pin names

The resolver assumes:

- pin connection points are already known
- wire polylines are explicit enough that endpoints and vertices capture intended
  branch points
- named nets are explicit `Net` objects anchored onto the sheet geometry
- power nets are just `Net(is_power=True)`, not a separate primitive
- hierarchical connectivity is expressed through `Symbol(kind="sheet")` pins
  matched by name to child-sheet `Sheet.pins`

This is enough to reconstruct a netlist for the common single-sheet cases we
need first.
