# faebryk.libs.eda

Shared EDA package layout.

The common layering is:

```text
file -> ll -> il -> hl

file  = on-disk format / parser-writer boundary
ll    = low-level, format-shaped model
il    = intermediate semantic model for one EDA
hl    = shared atopile-facing connectivity model
```

## Per-EDA Flow

```text
Altium PCB / schematic
----------------------
.PcbDoc / .SchDoc
    -> convert/*/file_ll.py
    -> models/*/ll.py
    -> convert/*/il_ll.py
    -> models/*/il.py
    -> convert/*/il_hl.py
    -> hl/models/*.py
    -> hl/convert/*.py
    -> Netlist
```

```text
KiCad PCB / schematic
---------------------
.kicad_pcb / .kicad_sch
    -> faebryk.libs.kicad.fileformats
    -> pyzig KiCad AST / IL
    -> eda/kicad/convert/*/il_hl.py
    -> hl/models/*.py
    -> hl/convert/*.py
    -> Netlist
```

## Intent

- Keep each EDA-specific format separate.
- Keep `hl/` EDA-agnostic.
- Make `il -> hl` the bridge into shared atopile connectivity logic.
- Preserve room for both directions later:
  `file <-> ll <-> il <-> hl`
