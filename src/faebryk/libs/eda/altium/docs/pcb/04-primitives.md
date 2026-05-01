# Altium Binary PcbDoc Format: Primitives

This document outlines the geometric primitives stored in the various `*6/Data` streams within `.PcbDoc` and `.PcbLib` files.

## Common Primitive Flags

Many graphical primitives share a common set of 16-bit flags (usually read early in the binary block) defining basic states. Based on `PcbBinaryConstants.cs`:

- **Bit 2 (`0x04`)**: Unlocked Flag (Inverted — `0` means the object is locked, `1` means unlocked).
- **Bit 5 (`0x20`)**: Tenting Top (e.g., for vias/pads).
- **Bit 6 (`0x40`)**: Tenting Bottom.
- **Bit 9 (`0x200`)**: Keepout region flag.

Primitives also share references to:
- **Layer**: Identifies the board layer (see Layer Enumeration).
- **Net Index**: A 16-bit index matching the net order in `Nets6` (or `65535` / `0xFFFF` for unconnected).
- **Component Index**: A 16-bit index linking the primitive to a component in `Components6` (or `0xFFFF` for free primitives).

---

## Component (`Components6`)

Components define the footprint instances placed on the PCB.
They are essentially parameter blocks followed by (or accompanied by) binary headers.

Key parsed properties include:
- **`X`, `Y`**: Position coordinates (often encoded as raw integers).
- **`LAYER`**: Layer ID.
- **`ROTATION`**: Rotation angle in degrees.
- **`LOCKED`**: Boolean indicating if the component is locked.
- **`SOURCEDESIGNATOR`**: The reference designator string (e.g., `U1`).
- **`SOURCEFOOTPRINTLIBRARY`** / **`PATTERN`**: Footprint library and pattern names.
- **`NAMEON`**, **`COMMENTON`**: Booleans indicating if the designator and comment text are visible.
- **`SOURCEUNIQUEID`**: The link to the schematic component.

---

## Pad (`Pads6`)

Pads are arguably the most complex primitive due to their multi-layer stacks, complex shapes, and drill properties. They are read sequentially as fixed-size binary blocks (often 114 bytes for the main block, followed by an optional 596-byte shape block).

### Basic Properties
- **Location**: `X`, `Y`.
- **Top / Middle / Bottom Sizes**: Dimensions (`X` and `Y`) of the pad on the respective layers.
- **Top / Middle / Bottom Shapes**: Pad shape byte (0=Round, 1=Rect, 2=Octagonal, 9=RoundRect/Slot).
- **Hole Size**: Drill diameter.
- **Rotation**: Angle in degrees.
- **Plated**: Boolean flag.

### Stack Modes (`PADMODE`)
- `0` (Simple): Same size and shape on all layers.
- `1` (Top-Middle-Bottom): Distinct dimensions for top, middle (all internal layers), and bottom.
- `2` (Full Stack): Individual sizes and shapes defined for up to 32 layers.

### Extended Pad Data (Masks and Planes)
- **Paste Mask / Solder Mask Expansions**: Often have explicit values and modes (`None`, `Rule`, `Manual`).
- **Power Plane Connect Style**: `Direct`, `Relief`, or `None`.
- **Relief Parameters**: Air gap, conductor width, and number of entries.
- **Drill Type / Hole Shape**: Round, Square, or Slot. If Slot, the `HoleSlotLength` and `HoleRotation` are parsed from the extended size block.

---

## Via (`Vias6`)

Vias share many attributes with pads but are generally simpler.
- **Position**: `X`, `Y`.
- **Hole Size**: Drill diameter.
- **Diameter**: Copper diameter.
- **Layer Start / Layer End**: The spanning layers of the via (e.g., Top Layer to Bottom Layer for a through-hole via, or inner layers for blind/buried).
- **Thermal Reliefs**: Air gap and conductor width/count, specific to the via.
- **Via Mode**: Similar to pad modes, can define per-layer diameters for complex via stacks.

---

## Track (`Tracks6`)

Tracks (lines) are used for routing, outlines, and general line drawing.
- **Start / End**: `X1`, `Y1` and `X2`, `Y2` coordinates.
- **Width**: Track thickness.
- **Layer**: Layer ID.
- **Net Index**: To associate the track segment with a net.
- **Polygon Outline Flag**: A boolean indicating if this track segment forms part of a polygon pour outline.

---

## Arc (`Arcs6`)

Circular arcs, used in routing, component outlines, and board shapes.
- **Center**: `X`, `Y`.
- **Radius**: Radius of the arc.
- **Start Angle / End Angle**: Defines the sweep of the arc in degrees.
- **Width**: Line thickness.
- **Layer** & **Net Index**.

---

## Text (`Texts6`)

Text primitives handle designators, comments, and free text.
- **Position**: `X`, `Y`.
- **Height** & **Stroke Width**: Dimensions of the text characters.
- **Rotation**: Angle in degrees.
- **Text Value**: The actual string. If it requires Unicode, it uses the `WIDESTRING_INDEX` pointing to the `WideStrings6` table.
- **Text Type**:
  - `0`: Stroke font (Default, Sans-Serif, Serif).
  - `1`: TrueType font (includes `FONTNAME`, `ISBOLD`, `ISITALIC`).
  - `2`: Barcode (includes `BARCODE_TYPE` like Code39 or Code128, and margins).
- **Justification**: Left-Top, Center-Center, Right-Bottom, etc.

---

## Polygon (`Polygons6`)

Polygons represent copper pours.
- **Layer** & **Net Index**.
- **Hatch Style**: Solid, 45-degree, 90-degree, Horizontal, Vertical, None.
- **Grid Size** & **Track Width**: Parameters for hatched polygons.
- **Pour Index**: Defines the priority of the pour compared to other overlapping polygons.
- **Vertices**: A list of coordinates forming the outer boundary of the polygon. (Often extracted from parameter keys like `VX0`, `VY0`, `KIND0`, `R0`, etc.)

---

## Region (`Regions6`)

Regions are solid copper areas, polygon cutouts, board cutouts, or custom keepouts.
- **Kind**: Copper, Polygon Cutout, Dashed Outline, Cavity, or Board Cutout.
- **Outline Vertices**: The outer boundary.
- **Holes**: An array of vertex arrays defining internal cutouts within the region.
- **Keepout Restrictions**: Bitfield defining which objects the keepout restricts (Tracks, Vias, Pads, etc.).
