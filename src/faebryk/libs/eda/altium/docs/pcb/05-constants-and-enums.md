# Altium Binary PcbDoc Format: Constants and Enums

This document lists the internal enumeration values and constants used throughout the `.PcbDoc` format, primarily derived from KiCad's `altium_parser_pcb.h` and AltiumSharp.

## Layer Identifiers (`ALTIUM_LAYER`)

Altium uses specific integer IDs to represent board layers. In newer versions (V7/V8), extended base offsets are used.

### Copper Layers


| Name          | ID  | Name                       | ID     |
| ------------- | --- | -------------------------- | ------ |
| `TopLayer`    | 1   | `MidLayer1` - `MidLayer30` | 2 - 31 |
| `BottomLayer` | 32  |                            |        |


### Masks and Silkscreen


| Name                      | ID  | Name            | ID  |
| ------------------------- | --- | --------------- | --- |
| `TopOverlay` (Silkscreen) | 33  | `BottomOverlay` | 34  |
| `TopPaste`                | 35  | `BottomPaste`   | 36  |
| `TopSolder` (Mask)        | 37  | `BottomSolder`  | 38  |


### Internal Planes


| Name                                 | ID      |
| ------------------------------------ | ------- |
| `InternalPlane1` - `InternalPlane16` | 39 - 54 |


### Mechanical and Other Layers


| Name                           | ID      |
| ------------------------------ | ------- |
| `DrillGuide`                   | 55      |
| `KeepOutLayer`                 | 56      |
| `Mechanical1` - `Mechanical16` | 57 - 72 |
| `DrillDrawing`                 | 73      |
| `MultiLayer`                   | 74      |
| `Connections`                  | 75      |
| `Background`                   | 76      |
| `DRCErrorMarkers`              | 77      |
| `Selections`                   | 78      |
| `VisibleGrid1` / `2`           | 79 / 80 |
| `PadHoles` / `ViaHoles`        | 81 / 82 |


### V7 / V8 Extended Layers

- **V7 Copper Base**: `0x01000000` (Top = `+1`, Bottom = `+65535`)
- **V7 Mechanical Base**: `0x01020000` (Mech1 = `+1`, Mech17 = `+17`)
- **V8 Other Base**: `0x01030000` (TopOverlay = `+6`, BottomSolder = `+11`, PadHoles = `+22`)

---

## Shape Enumerations

### Pad Shapes

- `0`: Unknown
- `1`: Circle
- `2`: Rectangle
- `3`: Octagonal
- `9`: Rounded Rectangle / Slot (Used in extended/alt shape arrays)

### Pad Hole Shapes

- `0`: Round
- `1`: Square
- `2`: Slot

---

## Rule Kinds (`ALTIUM_RULE_KIND`)

Identifies the type of design rule in the `Rules6` storage.

- `1`: Clearance
- `2`: Differential Pair Routing
- `3`: Height
- `4`: Hole Size
- `5`: Hole To Hole Clearance
- `6`: Width
- `7`: Paste Mask Expansion
- `8`: Solder Mask Expansion
- `9`: Plane Clearance
- `10`: Polygon Connect Style
- `11`: Routing Vias

---

## Other Properties

### Polygon Connect Style (`ALTIUM_CONNECT_STYLE`)

- `1`: Direct Connect
- `2`: Thermal Relief
- `3`: No Connect

### Polygon Hatch Style (`ALTIUM_POLYGON_HATCHSTYLE`)

- `1`: Solid (Pour)
- `2`: 45-Degree Hatch
- `3`: 90-Degree Hatch
- `4`: Horizontal
- `5`: Vertical
- `6`: None (Outline only)

### Text Position / Justification (`ALTIUM_TEXT_POSITION`)

Used for component designators, comments, and string bounding boxes.

- `0`: Manual (No auto-position)
- `1`: Left-Top
- `2`: Left-Center
- `3`: Left-Bottom
- `4`: Center-Top
- `5`: Center-Center
- `6`: Center-Bottom
- `7`: Right-Top
- `8`: Right-Center
- `9`: Right-Bottom

### Text Type (`ALTIUM_TEXT_TYPE`)

- `0`: Stroke (Vector) Font
- `1`: TrueType Font
- `2`: Barcode
