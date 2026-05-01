# Altium IL -> KiCad Bridge Checklist

- [x] Fix pad rotation inside rotated footprints. KiCad stores footprint-child rotation in board space, so IL pad rotation should not be made footprint-relative on emit.
- [x] Fix no-net mapping at the LL -> IL seam so unconnected pads, vias, tracks, and regions stay unconnected instead of collapsing onto the first net.
- [x] Fix primitive net indexing for real `.PcbDoc` files. Pads, vias, tracks, arcs, fills, and regions are now decoded with `0-based net refs` and `0xFFFF -> no net`, which fixes the bad pad-net assignments seen in `main.PcbDoc`.
- [x] Stop emitting duplicate visible `Reference`/`Value` properties when the Altium source already carries explicit component text; keep the visible text in `fp_text` instead.
- [x] Preserve default property visibility/layer placement better by hiding synthetic `Reference`/`Value` properties when explicit component text exists instead of hardcoding visible silkscreen properties.
- [x] Preserve source text geometry for visible component text by keeping Altium text as KiCad `fp_text` instead of inflating it through default property styling.
- [x] Classify component designator/value text semantically. Real designator/comment text now becomes KiCad `Reference` / `Value` properties with source geometry, placeholder literals such as `.Designator` become `${REFERENCE}`, and duplicate designator text is dropped.
- [x] Stop reverse-mapping Altium mechanical layers through the KiCad -> Altium table. Real mechanical text now lands on stable `User.*` layers instead of nonsense like `Edge.Cuts`.
- [x] Emit KiCad zone settings from available Altium IL semantics: net, keepout flags, clearance, and polygon-connect/thermal defaults when rules are present.
- [x] Emit slotted drills and other pad drill details in KiCad-compatible form.
- [x] Parse newer KiCad setup files with decimal `pad_to_mask_clearance` and multi-value pad `chamfer`, so imported KiCad boards can be structurally compared in tests.
- [x] Decode board thickness from real `Board6` data instead of defaulting to `0`. The bridge now falls back to the V9 layer-stack `COPTHICK` / `DIELHEIGHT` fields when `BOARDTHICKNESS` is absent, so `main.PcbDoc` emits `general.thickness ~= 0.41148` instead of `0.0`.
- [ ] Investigate missing edge-cut geometry and preserve it where the source IL actually carries outline or cutout data.
- [ ] Decode polygon-hole keepouts from the real Altium polygon streams. `main.PcbDoc` now emits the two GND copper pours with correct nets, but still misses the multi-layer keepout/cutout behavior that KiCad’s importer derives from the source file.
