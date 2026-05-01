# Altium SchDoc Research

## Scope

This schematic track follows the same architecture as the PCB exporter:

- `file <-> ll <-> il`
- `file`: OLE compound file streams
- `ll`: low-level schematic records, still close to the Altium parameter-block shape
- `il`: semantic schematic objects without `OWNERINDEX`-style wiring

## Local sources used

- `../../.local/AltiumSharp/src/OriginalCircuit.Altium/Serialization/Readers/SchDocReader.cs`
- `../../.local/AltiumSharp/src/OriginalCircuit.Altium/Serialization/Writers/SchDocWriter.cs`
- `../../.local/AltiumSharp/src/OriginalCircuit.Altium/Serialization/Readers/SchLibReader.cs`
- `../../.local/AltiumSharp/src/OriginalCircuit.Altium/Serialization/Writers/SchLibWriter.cs`
- `../../.local/AltiumSharp/src/OriginalCircuit.Altium/Models/Sch/*`
- `../../.local/AltiumSharp/TestData/*.SchDoc`

## On-disk SchDoc shape

Real `.SchDoc` files observed under `../../.local/AltiumSharp/TestData` are OLE compound files with these root streams:

- `FileHeader`
- `Additional`
- `Storage`

`FileHeader` is the main payload. It contains:

1. A document header parameter block, for example:
   - `HEADER=Protel for Windows - Schematic Capture Binary File Version 5.0`
   - `Weight`
   - `MinorVersion`
   - `UniqueID`
2. A flat sequence of schematic record parameter blocks.

`Additional` is another parameter block stream seen in real files. It is not written by AltiumSharp's `SchDocWriter`, so it should be preserved even if we do not interpret it semantically yet.

`Storage` holds compressed embedded image data. AltiumSharp matches those image payloads to `SchImage` primitives in encounter order.

## Parameter block encoding

The schematic record encoding is textual:

- 4-byte little-endian size prefix
- Windows-1252 encoded parameter string
- trailing NUL

The parameter string shape is:

```text
|KEY=VALUE|KEY2=VALUE2|...
```

Keys are effectively case-insensitive in AltiumSharp, but real files use mixed casing such as:

- `RECORD`
- `OwnerIndex`
- `Location.X`
- `UniqueID`
- `IsNotAccesible`

The implementation should preserve original key spelling when possible.

## Record ownership model

SchDoc stores primitives in a flat list. Hierarchy is reconstructed through `OWNERINDEX`.

- Components are `RECORD=1`
- Child primitives point at their parent component with `OWNERINDEX=<component record index>`
- There are also container records for implementation metadata:
  - `44` = `ImplementationList`
  - `45` = `Implementation`
  - `46` = `MapDefinerList`
  - `47` = `MapDefiner`
  - `48` = `ImplementationParameters`

For the initial pass, the LL must preserve these record blocks even if the IL does not model them yet.

## Record types that show up immediately in real SchDoc fixtures

From `Power Supply.SchDoc`:

- `1` component
- `2` pin
- `17` power object
- `25` net label
- `27` wire
- `29` junction
- `34` designator parameter
- `41` parameter
- `31` document-options style record
- `39` template/file-reference record
- `44` / `45` / `46` / `48` implementation hierarchy records

From `SPI Isolator.SchDoc`, the counts scale up heavily but the same families dominate.

## Coordinate conventions

AltiumSharp uses two coordinate encodings:

- DXP coordinate pairs:
  - `CoordFromDxp(dxp, frac) = dxp * 100000 + frac`
  - used by fields like `Location.X`, `Location.Y`, `Corner.X`
- schematic-unit vertex coordinates:
  - stored as raw coordinate / `1000`
  - used by wire, polyline, polygon, and bezier point lists

The low-level model should normalize both to raw integer coordinates.

## Initial semantic slice

The first IL pass only needs a stable subset that we can exercise with real fixtures:

- components
- pins
- parameters and designators
- wires
- net labels
- junctions
- power objects
- document header/additional/storage preservation

Everything else should remain preserved at LL even if not surfaced in IL yet.
