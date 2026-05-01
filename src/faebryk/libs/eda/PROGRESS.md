# EDA Progress

## Shared

- [x] Move shared EDA code to `src/faebryk/libs/eda`
- [x] Add shared HL schematic and PCB connectivity models
- [x] Add initial KiCad and Altium `il -> hl` converters

## KiCad Schematic

- [x] Treat the KiCad AST as the schematic IL boundary
- [x] Add schematic `il -> hl` conversion
- [x] Reconstruct netlists from wires, labels, buses, and hierarchical sheets
- [x] Validate reconstructed schematic netlists against `kicad-cli sch export netlist`
- [x] Preserve KiCad repeated-sheet instance references during HL conversion
- [~] Broaden edge-case coverage beyond the current demo corpus
  - Clean passes now include `m2-oculink-adapter` in addition to the original demo set
  - `artix-dc-scm` is reduced to unnamed-net differences
  - `lpddr5-testbed` now preserves repeated-sheet connectivity and refdes correctly; the remaining gap is mostly net-name selection plus a small I2C naming mismatch
  - `oculink-pcie-adapter` and `nest-mini-drop-in-pcb` are good v9 fixtures but still fail closed-loop netlist parity

## KiCad PCB

- [x] Treat the KiCad AST as the PCB IL boundary
- [x] Keep the PCB parser focused on KiCad v9
- [x] Support modern v9 footprint metadata such as geometry-less `property(...)`
- [x] Add PCB `il -> hl` conversion
- [x] Add v9 PCB parse -> HL -> netlist smoke tests
- [ ] Improve PCB connectivity reconstruction until PCB netlists match schematic netlists
- [ ] Add stronger PCB closed-loop validation against external ground truth
- [~] Reconcile KiCad footprint schema and binding issues
  - [x] make footprint `tags` representation consistent across library and PCB models
  - [x] make library and PCB footprint description naming consistent (`description` vs `descr`) without losing KiCad wire-format fidelity
  - [x] fix the raw parsed library-footprint temporary-wrapper lifetime failure in pyzig by retaining parent wrappers for nested structs and linked lists
  - [x] validate with `ato dev test --ci`
  - [~] parse every reachable `*.kicad_pcb` and `*.kicad_mod` under `../..` as a final corpus sweep
    - latest sweep: `188/348` parsed successfully (`43` modern v9 PCBs, plus footprints and older files that happen to fit the current schema)
    - `160` failed as expected schema/version mismatches or malformed inputs
    - `39` still abort the subprocess with `free(): invalid pointer`, which points at a separate Zig-side PCB decode/free bug on unsupported older files rather than the fixed footprint-wrapper issue

## Altium Schematic

- [x] Research the SchDoc format and existing AltiumSharp implementation
- [x] Add initial `file -> ll -> il` layering
- [x] Add initial schematic docs and focused tests
- [~] Add schematic `il -> hl`
  - Flat explicit-wire cases work after coordinate normalization and pin-endpoint fixes
  - Real `Power Supply.SchDoc` now reconstructs 5 sensible nets through the full chain
  - Real `Overview.SchDoc` now loads as a 4-sheet HL schematic instead of a flattened single document
  - Child-sheet ports are now instantiated with parent-resolved interface names where needed for hierarchy binding
  - Real `Archimajor.SchDoc` now reaches full netlist parity against the reference OrCAD-style `.NET` import
  - Auto-discovered same-stem `.NET` / `.SchDoc` corpus coverage is now green through the end-to-end `SchDoc -> HL -> netlist` pipeline
- [ ] Reach PCB-level parity for import/export and validation
- [ ] Altium schematic roadmap
  - [~] Phase 1: add hierarchy primitives to LL/IL
    - [x] `SheetSymbol` (`15`)
    - [x] `SheetEntry` (`16`)
    - [x] `Port` (`18`)
    - [x] preserve sheet name / child file references from owned records `32` / `33`
  - [~] Phase 2: make Altium schematic loading project-aware
    - [x] top-sheet `.SchDoc` now recurses into child `.SchDoc` files referenced by sheet symbols
    - [x] `Schematic.decode(path)` now produces a multi-sheet HL model for hierarchical fixtures
    - [ ] add stronger project-resolution handling beyond sibling-file references
  - [x] Phase 3: map hierarchy cleanly into HL
    - [x] sheet symbols become `Symbol(kind="sheet")`
    - [x] sheet entries become parent-sheet interface pins
    - [x] ports become child-sheet pins
    - [x] child-sheet linkage now works by resolved file reference plus port/entry names
  - [~] Phase 4: add Altium bus support end-to-end
    - [x] `Bus` (`26`) now decodes through `file -> ll -> il -> hl`
    - [x] parent-sheet bus breakout detection feeds the shared HL bus solver
    - [ ] identify and model any remaining bus-entry / harness-related primitives outside the Archimajor patterns
  - [~] Phase 5: add hidden and implicit connectivity
    - [x] fix repeated-sheet hierarchy binding across parent sheet entries and child ports
    - [x] eliminate duplicated/floating shadow pins from Altium component pin variants
    - [ ] decode pin hidden-net metadata
    - [ ] model off-sheet / implicit net naming precedence correctly
  - [x] Phase 6: validate against real local corpus
    - flat fixtures like `Power Supply.SchDoc`
    - port-heavy fixtures like `DAC.SchDoc`
    - hierarchical fixtures like `Overview.SchDoc`
    - [x] correctness metric reached for Archimajor terminal-set parity first
    - [x] canonical net naming now matches the Archimajor reference side too

### Archimajor Endboss

- Goal: make `Archimajor.SchDoc -> hl -> netlist` electrically equivalent to `Z__altium_Mirror-test_Archimajor.NET -> hl -> netlist`
- Acceptance: exact terminal-set parity first, net naming second
- Commit discipline: commit all touched files after every stage
- Subagents: use them for `.NET` grammar analysis, Archimajor hierarchy/bus inventory, and mismatch triage whenever a stage can be parallelized
- [~] Stage 0/1: trustworthy reference side and shared diff harness
  - [x] identify the real OrCAD PCB II `.NET` fixture format used by Archimajor
  - [x] add a real component-centric Cadence LL parser for the local `.NET` files
  - [x] add HL netlist diff helpers for terminal-set comparison and stable reports
  - [x] add fixture-backed Archimajor smoke coverage for both pipelines
  - [x] capture the first meaningful baseline after fixing the reference parser
    - reference netlist: `685` nets
    - current Altium-derived netlist: `609` nets
    - terminal-set diff: `missing=525`, `extra=289`, `name_mismatches=111`
  - [x] promote the final terminal-set assertion from `xfail` once parity is reached
- [x] Stage 2: lock Archimajor project loading
  - [x] make top-sheet and child-sheet resolution deterministic
  - [x] resolve child sheet filenames case-insensitively for the local Mirror-test corpus
  - [x] instantiate repeated child sheets as concrete sheet instances instead of collapsing them by source path
  - [x] keep duplicate child references to the same `.SchDoc` distinct by parent symbol index
  - [x] add tests for expected Archimajor sheet topology
- [x] Stage 3: fix hierarchical connectivity
  - [x] repeated-sheet separation and stable sheet identity
  - [x] repeated child-sheet component refs now expand to instance suffixes like `U15A..H` and `U13A..E`
  - [x] repeated local child nets now expand to instance suffixes like `CA1A..H` and `ENCAA..H`
  - [x] parent sheet entries <-> child sheet ports now bind through repeated/bus-aware name resolution
  - [x] top-level repeated interface nets like `DIR1..8`, `STEP1..8`, and `DIAG1..8` now carry the parent-side microcontroller terminals
- [x] Stage 4: implement Altium bus connectivity
  - bus objects now participate in HL conversion and parent-sheet bus breakout detection
  - Archimajor repeated bus interfaces are scalarized correctly enough to hit terminal-set parity
- [x] Stage 5: close implicit connectivity gaps
  - supply-rail connectivity and repeated hierarchical power links now match the reference terminal sets
  - duplicate shadow-pin variants no longer create floating phantom nets
- [x] Stage 6: naming reconciliation and final parity
  - [x] exact Archimajor terminal-set parity reached
  - [x] repeated-sheet numeric names now canonicalize to the reference alpha form where appropriate
  - [x] Altium canonical naming now prefers the reference-style specific names and drops trivial aliases
  - [x] single-pin NC nets now reproduce the reference `?N` placeholder style
  - current endboss state: `missing=0`, `extra=0`, `name_mismatches=0`
- [x] Same-stem local reference corpus
  - [x] discover same-stem `.NET` / `.SchDoc` pairs automatically at test time
  - [x] make the Cadence `.NET` parser preserve short-package designators like `TP 1V2_ TP` and `PAD07 12VH Test point`
  - [x] remove temporary `xfail` handling from the corpus test
  - [x] enforce the strongest honest invariant per pair
    - all discovered pairs must load
    - all discovered pairs must cover the reference terminal set
    - pairs with a real connectivity oracle, exact terminal sets, and no unresolved child sheets must match connectivity
    - full equality is only required for pairs not in the current `not fully supported` basename list
  - current discovered corpus state
    - `Archimajor`: full netlist equality
    - `TOP`: connectivity parity, naming gap reduced to 5 remaining name mismatches
    - `main` and `PG_V0`: reference `.NET` files are per-pin listings, so they currently act as terminal-coverage oracles rather than connectivity oracles
    - `Root_page`: upstream EsoCore fixture still lacks `Tag.SchDoc`, so the local pair currently acts as a reference-terminal-coverage check rather than a full project-parity check

## Altium PCB

- [x] Establish `file -> ll -> il -> hl` layering
- [x] Add explicit `ll <-> file` and `ll <-> il` converters
- [x] Add roundtrip and fidelity tests at the file and low-level boundaries
- [x] Add Altium PCB `il -> hl`
- [x] Produce openable KiCad boards from Altium PCB input
- [ ] Improve connectivity and semantic parity against KiCad's native import
- [ ] Continue closing remaining geometry, zone, and netlist gaps

## Notes

- KiCad PCB support is intentionally v9-only.
- Older KiCad PCB dialects are not a target for `pcb.zig`.
- The remaining KiCad PCB gap is not parsing; it is connectivity fidelity.
- Latest full validation run: `ato dev test --ci` -> `2551 passed, 0 failed, 88 skipped`.
