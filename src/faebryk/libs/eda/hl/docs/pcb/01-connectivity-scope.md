# PCB HL Scope

The prototype PCB HL keeps only what is needed for netlist reconstruction:

- recursive `Collection`s
- conductive geometries with layer membership
- optional explicit net assignment on geometries
- terminal identity carried by collection metadata

In practice the current convert layer understands three conductive shape kinds:

- `Circle`
- `Segment`
- `Polygon`

This first version solves a narrow but useful subset of geometry:

- circle attachment by overlap
- segment endpoint and collinear attachment
- multilayer stitching for geometries that share an anchor
- point-in-polygon attachment

Terminal refs are reconstructed from nested collections:

- a component-like collection carries owner metadata such as `refdes`
- a child collection carries `terminal_id`
- conductive geometries under that child collection become netlist terminals

This is still prototype-level geometry, but it is enough to let named
conductors propagate a net identity onto otherwise unnamed terminals.
