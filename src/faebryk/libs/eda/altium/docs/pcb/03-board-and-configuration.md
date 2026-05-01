# Altium Binary PcbDoc Format: Board and Configuration

This document outlines how high-level board metadata, layer stackups, and configuration rules are encoded in the `.PcbDoc` format.

## `Board6/Data` Stream

The `Board6` storage contains the primary board metadata inside its `Data` stream. The data is encoded as a **Parameter Block** followed by binary outline data in some versions.

### Board Attributes
Key properties typically found in the `Board6` parameter block:
- `SHEETX`, `SHEETY`: The origin/position of the design sheet.
- `SHEETWIDTH`, `SHEETHEIGHT`: Dimensions of the design sheet.
- Layer count and definition arrays.

### Layer Stackup
The board parameter block defines the physical PCB stackup through numbered layer prefixes (e.g., `LAYER0_`, `LAYER1_`).

For each layer index, you may find:
- **`LAYERID`**: The numeric layer identifier (see `ALTIUM_LAYER`).
- **`NAME`**: User-defined name of the layer.
- **`COPPERTHICK`**: Copper thickness.
- **`DIELECTRICCONST`**: Dielectric constant.
- **`DIELECTRICTHICK`**: Dielectric thickness.
- **`DIELECTRICMATERIAL`**: Dielectric material string.
- **`MECHENABLED`**: Boolean indicating if a mechanical layer is active.
- **`MECHKIND`**: The assigned mechanical purpose (e.g., `AssemblyTop`, `CourtyardBottom`, `3DBodyTop`).

### Board Outline (Vertices)
In addition to the parameter block, the board shape outline is often defined by a series of polygon vertices (numbered `VX0`, `VY0`, `VX1`, `VY1`, etc.) within the properties. Each vertex may include:
- `VXn`, `VYn`: X and Y coordinates.
- `KINDn`: Whether the vertex is a line or arc (0 = straight, 1 = arc).
- `Rn`, `SAn`, `EAn`: Radius, Start Angle, and End Angle if the segment is an arc.
- `CXn`, `CYn`: Center of the arc.

## `Nets6/Data` Stream

The `Nets6` storage defines the nets in the PCB. It consists of multiple parameter blocks, each defining one net.

Typical properties per net:
- `NAME`: The name of the net (e.g., `GND`, `VCC`, `NetC1_1`).
- The order of nets in the file defines the internal **Net ID** (a zero-based index) used by primitives (Pads, Tracks, Vias) to associate with a net. An index of `65535` (`0xFFFF`) generally represents "Unconnected".

## `Rules6/Data` Stream

The `Rules6` storage contains the Design Rules applied to the board. Like nets, these are stored as parameter blocks.

### Common Rule Properties
- **`NAME`**: The name of the rule.
- **`PRIORITY`**: Rule evaluation priority.
- **`KIND`**: The rule type identifier (e.g., Clearance, Width).
- **`SCOPE1EXPR`**, **`SCOPE2EXPR`**: The query expressions defining what the rule applies to (e.g., `IsPad`, `InNet('GND')`).

### Common Rule Kinds
Depending on the `KIND`, the rule block will contain specific parameters:
- **Clearance (`KIND=1`)**: Defines `GAP` (Clearance gap).
- **Width (`KIND=6`)**: Defines `MINLIMIT`, `MAXLIMIT`, and `PREFERREDWIDTH`.
- **Hole Size (`KIND=4`)**: Defines min/max hole limits.
- **Routing Vias (`KIND=11`)**: Defines `WIDTH`, `MINWIDTH`, `MAXWIDTH` for the via diameter, and `HOLEWIDTH`, `MINHOLEWIDTH`, `MAXHOLEWIDTH` for the drill.
- **Solder Mask Expansion (`KIND=8`)**: Defines `EXPANSION`.
- **Paste Mask Expansion (`KIND=7`)**: Defines `EXPANSION`.
- **Polygon Connect Style (`KIND=10`)**: Defines `AIRGAPWIDTH`, `RELIEFCONDUCTORWIDTH`, `RELIEFENTRIES`, and `CONNECTSTYLE` (Direct, Relief, or None).

## `Classes6/Data` Stream

Design classes (Net Classes, Component Classes, Pad Classes, etc.) are defined here.
- **`NAME`**: Name of the class.
- **`UNIQUEID`**: A unique string identifier.
- **`KIND`**: Class kind (Net=0, Component=1, From-To=2, Pad=3, Layer=4, DiffPair=6, Polygon=7).
- Contains an array of members, typically defined by properties like `NAME0`, `NAME1`, etc., pointing to the members of the class.
