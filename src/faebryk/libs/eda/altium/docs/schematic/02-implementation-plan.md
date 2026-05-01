# Altium Schematic Implementation Plan

## Goal

Establish the same layered architecture as the PCB work:

- `file <-> ll <-> il`

with tests that prove:

- low-level SchDoc parsing works on local AltiumSharp fixtures
- the first semantic subset is translated both ways
- synthetic export can write valid compound-file `.SchDoc` output

## Phase 1

- Create `models/schematic/ll.py`
  - flat low-level record model
  - explicit typed records for the core primitives we will use immediately
  - `UnknownRecord` passthrough for everything else
- Create `models/schematic/il.py`
  - semantic document/component primitives
  - component child ownership instead of `OWNERINDEX`
- Create `convert/schematic/file_ll.py`
  - parameter-block decoder/encoder
  - compound-file reader/writer entrypoints
  - typed record codecs for:
    - component
    - pin
    - parameter/designator
    - wire
    - net label
    - junction
    - power object
  - generic passthrough codec for unsupported records
- Create `convert/schematic/il_ll.py`
  - `ll -> il` ownership reconstruction
  - `il -> ll` flattening with component children written after their parent

## Tests for phase 1

- synthetic `serialize_schdoc() -> deserialize_schdoc()` roundtrip
- synthetic `.SchDoc` write/read using the existing CFB writer
- real fixture `file -> ll` smoke on local AltiumSharp `.SchDoc`
- `ll -> il` ownership test showing component pins/parameters become component children
- `il -> ll` flattening test showing child records regain `OWNERINDEX`

## Explicitly deferred

- SchLib support
- full schematic graphics coverage
- implementation/map-definer IL modeling
- image payload association beyond raw storage preservation
- KiCad schematic conversion
- high-fidelity unchanged passthrough similar to the PCB fidelity work

## Exit criteria for this first slice

- local fixture parse works
- core primitives are represented in both LL and IL
- export writes a syntactically valid compound file with `FileHeader`
- tests give us a stable base to expand into the rest of the schematic record catalog
