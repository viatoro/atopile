"""HL round-trip tests for DeepPCB ↔ HL conversion.

The primary acceptance criterion: round-tripping through DeepPCB produces
**zero diff** at the HL level for components, pads, traces, vias, and zones.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from faebryk.libs.eda.deeppcb.convert import file_ll
from faebryk.libs.eda.deeppcb.convert.il_hl import convert_hl_to_ll, convert_ll_to_hl
from faebryk.libs.eda.deeppcb.models import ll
from faebryk.libs.eda.deeppcb.tests.constants import (
    ALL_BOARDS,
    BOARD_EXAMPLE,
    BOARD_EXPECTATIONS,
    TOLERANCE,
    rect_outline,
)
from faebryk.libs.eda.hl.models.pcb import (
    PCB,
    Circle,
    Collection,
    ConductiveGeometry,
    LayerID,
    NetID,
    Obround,
    Point2D,
    Polygon,
    Rectangle,
    RoundedRectangle,
    Segment,
    Shape2D,
    SourceID,
)

# ---------------------------------------------------------------------------
# Shared HL comparison
# ---------------------------------------------------------------------------


def _approx(a: float, b: float) -> bool:
    return abs(a - b) < TOLERANCE


def _points_approx(a: Point2D, b: Point2D) -> bool:
    return _approx(a.x, b.x) and _approx(a.y, b.y)


def _compare_shapes(a: Shape2D, b: Shape2D) -> bool:
    """Approximate equality for two HL shapes."""
    if type(a) is not type(b):
        return False
    if isinstance(a, Circle) and isinstance(b, Circle):
        return _approx(a.radius, b.radius)
    if isinstance(a, Rectangle) and isinstance(b, Rectangle):
        return (
            _approx(a.width, b.width)
            and _approx(a.height, b.height)
            and _approx(a.rotation_deg % 360, b.rotation_deg % 360)
        )
    if isinstance(a, RoundedRectangle) and isinstance(b, RoundedRectangle):
        return (
            _approx(a.width, b.width)
            and _approx(a.height, b.height)
            and _approx(a.corner_radius, b.corner_radius)
            and _approx(a.rotation_deg % 360, b.rotation_deg % 360)
        )
    if isinstance(a, Obround) and isinstance(b, Obround):
        return (
            _approx(a.width, b.width)
            and _approx(a.height, b.height)
            and _approx(a.rotation_deg % 360, b.rotation_deg % 360)
        )
    if isinstance(a, Segment) and isinstance(b, Segment):
        return _points_approx(a.start, b.start) and _points_approx(a.end, b.end)
    if isinstance(a, Polygon) and isinstance(b, Polygon):
        if len(a.vertices) != len(b.vertices):
            return False
        return all(_points_approx(va, vb) for va, vb in zip(a.vertices, b.vertices))
    return False


def _copper_layers(layers: list[LayerID]) -> set[str]:
    """Filter to copper layers only — DeepPCB doesn't model mask/paste/silk.

    Wildcard layers (``*.Cu``, ``F&B.Cu``) are kept as-is; both the wildcard
    and its expansion are treated as matching during comparison.
    """
    return {
        layer.name
        for layer in layers
        if layer.name.endswith(".Cu")
        or layer.name.startswith("In")
        or layer.name in ("*.Cu", "F&B.Cu")
    }


def _copper_layers_match(a: list[LayerID], b: list[LayerID]) -> bool:
    """Compare copper layers allowing wildcard expansion.

    ``*.Cu`` matches any set of real copper layers; ``F&B.Cu`` matches
    ``{F.Cu, B.Cu}``.
    """
    la = _copper_layers(a)
    lb = _copper_layers(b)
    if la == lb:
        return True

    # Expand wildcards for comparison
    def _expand(s: set[str], other: set[str]) -> set[str]:
        result = set()
        for name in s:
            if name == "*.Cu":
                # Matches all real copper in the other set
                result.update(n for n in other if n not in ("*.Cu", "F&B.Cu"))
            elif name == "F&B.Cu":
                result.update({"F.Cu", "B.Cu"})
            else:
                result.add(name)
        return result

    return _expand(la, lb) == _expand(lb, la)


def _compare_geometry(a: ConductiveGeometry, b: ConductiveGeometry) -> bool:
    if not _points_approx(a.location, b.location):
        return False
    if not _compare_shapes(a.shape, b.shape):
        return False
    if not _copper_layers_match(a.layers, b.layers):
        return False
    a_net = a.net.name if a.net else None
    b_net = b.net.name if b.net else None
    return a_net == b_net


def _geometry_sort_key(g: ConductiveGeometry) -> tuple:
    """Deterministic ordering for geometry comparison."""
    net = g.net.name if g.net else ""
    layers = ",".join(sorted(layer.name for layer in g.layers))
    return (
        type(g.shape).__name__,
        round(g.location.x, 3),
        round(g.location.y, 3),
        layers,
        net,
    )


def assert_hl_equal(hl1: PCB, hl2: PCB, *, check_wire_widths: bool = True) -> None:
    """Assert two HL PCB models match across components, pads, and board geometries.

    Raises ``AssertionError`` with a descriptive message on the first mismatch.
    """
    # --- Collections (components) ---
    # Components with no pads (logos, board outlines) are dropped during
    # the DeepPCB round-trip because they have no electrical significance.
    hl1_with_pads = [c for c in hl1.collections if c.collections]
    hl2_with_pads = [c for c in hl2.collections if c.collections]
    assert len(hl1_with_pads) == len(hl2_with_pads), (
        f"Component count (with pads): {len(hl1_with_pads)} vs {len(hl2_with_pads)}"
    )

    # Match components by refdes (stable across formats) rather than source ID
    # (which may change — KiCad uses UUIDs, DeepPCB uses refdes).
    def _comp_key(c: Collection) -> str:
        return str(c.extra_properties.get("refdes", c.id.id if c.id else ""))

    comps1 = {_comp_key(c): c for c in hl1_with_pads}
    comps2 = {_comp_key(c): c for c in hl2_with_pads}

    for comp_key, c1 in comps1.items():
        assert comp_key in comps2, f"Component {comp_key!r} missing after round-trip"
        c2 = comps2[comp_key]

        # pads
        assert len(c1.collections) == len(c2.collections), (
            f"Component {comp_key} pad count: {len(c1.collections)} vs "
            f"{len(c2.collections)}"
        )

        # Sort pads by location — terminal_ids are not always stable across
        # formats (KiCad unnamed pads get UUIDs, DeepPCB assigns names).
        def _pad_loc_key(p: Collection) -> tuple:
            if p.geometries:
                loc = p.geometries[0].location
                return (round(loc.x, 2), round(loc.y, 2))
            return (0.0, 0.0)

        pads1 = sorted(c1.collections, key=_pad_loc_key)
        pads2 = sorted(c2.collections, key=_pad_loc_key)

        for p1, p2 in zip(pads1, pads2):
            assert len(p1.geometries) == len(p2.geometries), (
                f"Pad {p1.id} geom count: {len(p1.geometries)} vs {len(p2.geometries)}"
            )
            for g1, g2 in zip(p1.geometries, p2.geometries):
                assert _points_approx(g1.location, g2.location), (
                    f"Pad {p1.id} location: ({g1.location.x},{g1.location.y}) "
                    f"vs ({g2.location.x},{g2.location.y})"
                )
                assert type(g1.shape) is type(g2.shape), (
                    f"Pad {p1.id} shape type: {type(g1.shape).__name__} vs "
                    f"{type(g2.shape).__name__}"
                )
                assert _compare_shapes(g1.shape, g2.shape), (
                    f"Pad {p1.id} shape mismatch: {g1.shape} vs {g2.shape}"
                )
                assert _copper_layers_match(g1.layers, g2.layers), (
                    f"Pad {p1.id} layers: {_copper_layers(g1.layers)} vs "
                    f"{_copper_layers(g2.layers)}"
                )
                g1_net = g1.net.name if g1.net else None
                g2_net = g2.net.name if g2.net else None
                # Unnamed/mounting pads (padN) lose their net through
                # DeepPCB since they're not electrically meaningful.
                tid = p1.extra_properties.get("terminal_id", "")
                is_unnamed = not tid or str(tid).startswith("pad")
                if not is_unnamed:
                    assert g1_net == g2_net, (
                        f"Pad {p1.id} net: {g1_net!r} vs {g2_net!r}"
                    )

    # --- Board-level geometries (traces, vias, zones) ---
    assert len(hl1.geometries) == len(hl2.geometries), (
        f"Board geometry count: {len(hl1.geometries)} vs {len(hl2.geometries)}"
    )

    geoms1 = sorted(hl1.geometries, key=_geometry_sort_key)
    geoms2 = sorted(hl2.geometries, key=_geometry_sort_key)

    for i, (g1, g2) in enumerate(zip(geoms1, geoms2)):
        assert type(g1.shape) is type(g2.shape), (
            f"Geometry[{i}] shape type: {type(g1.shape).__name__} vs "
            f"{type(g2.shape).__name__}"
        )
        assert _compare_geometry(g1, g2), (
            f"Geometry[{i}] mismatch: "
            f"shape={type(g1.shape).__name__} loc=({g1.location.x:.3f},"
            f"{g1.location.y:.3f}) "
            f"net={g1.net.name if g1.net else None!r}"
        )

    # --- Wire widths ---
    if check_wire_widths:
        ww1 = hl1.extra_properties.get("_wire_widths", [])
        ww2 = hl2.extra_properties.get("_wire_widths", [])
        assert ww1 == ww2, f"Wire widths differ: {len(ww1)} vs {len(ww2)} entries"


# ---------------------------------------------------------------------------
# DeepPCB LL → HL → DeepPCB LL → HL round-trip
# ---------------------------------------------------------------------------


def test_ll_hl_round_trip():
    """Load DeepPCB → HL → DeepPCB → HL, verify HL models match."""
    if not BOARD_EXAMPLE.exists():
        pytest.skip("example board not found")

    board1 = file_ll.load(BOARD_EXAMPLE)
    hl1 = convert_ll_to_hl(board1)

    board2 = convert_hl_to_ll(hl1, board1.resolution)
    hl2 = convert_ll_to_hl(board2)

    assert_hl_equal(hl1, hl2)


# ---------------------------------------------------------------------------
# KiCad → HL → DeepPCB → HL end-to-end tests
# ---------------------------------------------------------------------------


def _load_kicad_hl(path: Path) -> PCB:
    from faebryk.libs.eda.kicad.convert.pcb.il_hl import convert_pcb_il_to_hl
    from faebryk.libs.kicad.fileformats import kicad

    pcb_file = kicad.loads(kicad.pcb.PcbFile, path)
    return convert_pcb_il_to_hl(pcb_file.kicad_pcb)


def _kicad_deeppcb_round_trip(kicad_path: Path) -> tuple[PCB, PCB]:
    """KiCad → HL₁ → DeepPCB LL → JSON → DeepPCB LL → HL₂.  Returns (HL₁, HL₂)."""
    hl1 = _load_kicad_hl(kicad_path)
    ll_board = convert_hl_to_ll(hl1)
    json_text = file_ll.dump(ll_board)
    ll_board2 = file_ll.load(json_text)
    hl2 = convert_ll_to_hl(ll_board2)
    return hl1, hl2


@pytest.mark.parametrize("kicad_path", ALL_BOARDS)
def test_kicad_round_trip(kicad_path: Path):
    """KiCad → HL → DeepPCB → HL round-trip, verify HL models match."""
    if not kicad_path.exists():
        pytest.skip("example not found")

    hl1, hl2 = _kicad_deeppcb_round_trip(kicad_path)

    # Sanity-check source data
    expect = BOARD_EXPECTATIONS[kicad_path]
    assert len(hl1.collections) >= expect.min_components
    assert len(hl1.geometries) >= expect.min_geometries

    assert_hl_equal(hl1, hl2, check_wire_widths=False)


# ---------------------------------------------------------------------------
# Synthetic HL round-trip (all shape types + board geometries)
# ---------------------------------------------------------------------------


def test_synthetic_hl_round_trip():
    """Build a synthetic HL model, round-trip through DeepPCB, verify match."""
    pcb = PCB(id=SourceID(id="test-board"), outline=rect_outline())

    # Component with various pad shapes
    comp = Collection(
        id=SourceID(id="U1"),
        extra_properties={"refdes": "U1", "name": "TestIC", "side": "FRONT"},
    )

    pad_shapes: list[tuple[str, Shape2D]] = [
        ("1", Circle(center=Point2D(x=0, y=0), radius=0.15)),
        (
            "2",
            Rectangle(
                center=Point2D(x=0, y=0), width=0.6, height=0.4, rotation_deg=0.0
            ),
        ),
        (
            "3",
            Obround(center=Point2D(x=0, y=0), width=0.3, height=0.6, rotation_deg=0.0),
        ),
        (
            "4",
            RoundedRectangle(
                center=Point2D(x=0, y=0),
                width=0.5,
                height=0.3,
                corner_radius=0.05,
                rotation_deg=0.0,
            ),
        ),
    ]

    for i, (pin_id, shape) in enumerate(pad_shapes):
        comp.collections.append(
            Collection(
                id=SourceID(id=f"U1:{pin_id}"),
                extra_properties={
                    "terminal_id": pin_id,
                    "terminal_kind": "pcb_pad",
                },
                geometries=[
                    ConductiveGeometry(
                        shape=shape,
                        location=Point2D(x=10.0 + i * 1.0, y=20.0),
                        layers=[LayerID(name="F.Cu")],
                        net=NetID(name=f"net_{pin_id}"),
                    )
                ],
            )
        )

    pcb.collections.append(comp)

    # Trace
    pcb.geometries.append(
        ConductiveGeometry(
            shape=Segment(
                start=Point2D(x=10.0, y=20.0),
                end=Point2D(x=11.0, y=20.0),
            ),
            location=Point2D(x=0.0, y=0.0),
            layers=[LayerID(name="F.Cu")],
            net=NetID(name="net_1"),
        )
    )

    # Via (multi-layer circle)
    pcb.geometries.append(
        ConductiveGeometry(
            shape=Circle(center=Point2D(x=0.0, y=0.0), radius=0.3),
            location=Point2D(x=10.5, y=20.5),
            layers=[LayerID(name="F.Cu"), LayerID(name="B.Cu")],
            net=NetID(name="net_2"),
        )
    )

    # Zone (polygon)
    pcb.geometries.append(
        ConductiveGeometry(
            shape=Polygon(
                vertices=[
                    Point2D(x=9.0, y=19.0),
                    Point2D(x=14.0, y=19.0),
                    Point2D(x=14.0, y=21.0),
                    Point2D(x=9.0, y=21.0),
                ]
            ),
            location=Point2D(x=0.0, y=0.0),
            layers=[LayerID(name="F.Cu")],
            net=NetID(name="GND"),
        )
    )

    # Round-trip
    ll_board = convert_hl_to_ll(pcb)
    json_text = file_ll.dump(ll_board)
    ll_board2 = file_ll.load(json_text)
    hl2 = convert_ll_to_hl(ll_board2)

    assert_hl_equal(pcb, hl2, check_wire_widths=False)


# ---------------------------------------------------------------------------
# atopile address round-trip
# ---------------------------------------------------------------------------


def test_ato_address_round_trip():
    """Verify atopile addresses survive the round-trip."""
    pcb = PCB(id=SourceID(id="ato-test"), outline=rect_outline())

    comp = Collection(
        id=SourceID(id="U2@vdiv.r1"),
        extra_properties={
            "refdes": "U2",
            "name": "Resistor",
            "ato_address": "vdiv.r1",
            "side": "FRONT",
        },
    )
    comp.collections.append(
        Collection(
            id=SourceID(id="U2@vdiv.r1:1"),
            extra_properties={"terminal_id": "1", "terminal_kind": "pcb_pad"},
            geometries=[
                ConductiveGeometry(
                    shape=Rectangle(
                        center=Point2D(x=0, y=0),
                        width=0.5,
                        height=0.3,
                    ),
                    location=Point2D(x=50.0, y=50.0),
                    layers=[LayerID(name="F.Cu")],
                )
            ],
        )
    )
    pcb.collections.append(comp)

    ll_board = convert_hl_to_ll(pcb)
    assert any("@" in c.id for c in ll_board.components)

    hl2 = convert_ll_to_hl(ll_board)
    comp2 = hl2.collections[0]
    assert comp2.extra_properties.get("refdes") == "U2"
    assert comp2.extra_properties.get("ato_address") == "vdiv.r1"


# ---------------------------------------------------------------------------
# Obround rotation regression
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "width,height,rotation",
    [
        (0.36, 0.61, 0.0),  # Vertical obround (h > w)
        (0.61, 0.36, 0.0),  # Horizontal obround (w > h) — the bug case
        (0.36, 0.61, 45.0),  # Rotated vertical
        (0.61, 0.36, -90.0),  # Rotated horizontal
        (0.5, 0.5, 0.0),  # Square obround (degenerate)
    ],
)
def test_obround_rotation_round_trip(width, height, rotation):
    """Verify obround width/height/rotation survive the round-trip exactly."""
    pcb = PCB(id=SourceID(id="obround-test"), outline=rect_outline())
    comp = Collection(
        id=SourceID(id="U1"),
        extra_properties={"refdes": "U1", "name": "test", "side": "FRONT"},
    )
    comp.collections.append(
        Collection(
            id=SourceID(id="U1:1"),
            extra_properties={"terminal_id": "1", "terminal_kind": "pcb_pad"},
            geometries=[
                ConductiveGeometry(
                    shape=Obround(
                        center=Point2D(x=0, y=0),
                        width=width,
                        height=height,
                        rotation_deg=rotation,
                    ),
                    location=Point2D(x=50.0, y=50.0),
                    layers=[LayerID(name="F.Cu")],
                    net=NetID(name="net1"),
                )
            ],
        )
    )
    pcb.collections.append(comp)

    ll_board = convert_hl_to_ll(pcb)
    hl2 = convert_ll_to_hl(ll_board)

    pad2 = hl2.collections[0].collections[0].geometries[0]
    shape2 = pad2.shape
    assert isinstance(shape2, Obround), f"Expected Obround, got {type(shape2)}"
    assert _approx(shape2.width, width), f"Width mismatch: {shape2.width} vs {width}"
    assert _approx(shape2.height, height), (
        f"Height mismatch: {shape2.height} vs {height}"
    )
    assert _approx(shape2.rotation_deg % 360, rotation % 360), (
        f"Rotation mismatch: {shape2.rotation_deg} vs {rotation}"
    )


# ---------------------------------------------------------------------------
# Wire width preservation
# ---------------------------------------------------------------------------


def test_wire_width_round_trip():
    """Verify wire widths survive the DeepPCB round-trip."""
    board = ll.DeepPCBBoard(
        name="wire-width-test",
        resolution=ll.Resolution(unit="mm", value=1000),
        boundary=ll.Boundary(
            shape=ll.Shape(
                type="polyline",
                points=[[0, 0], [100000, 0], [100000, 100000], [0, 100000], [0, 0]],
            ),
            clearance=200,
        ),
        padstacks=[],
        component_definitions=[],
        components=[],
        layers=[ll.Layer(id="F.Cu", keepouts=[])],
        nets=[ll.Net(id="net1", pins=[])],
        net_classes=[
            ll.NetClass(id="default", nets=[], clearance=200, track_width=200)
        ],
        planes=[],
        wires=[
            ll.Wire(
                net_id="net1",
                layer=0,
                start=[10000, 20000],
                end=[30000, 20000],
                width=250,
            ),
            ll.Wire(
                net_id="net1",
                layer=0,
                start=[30000, 20000],
                end=[30000, 40000],
                width=150,
            ),
        ],
        vias=[],
        via_definitions=[],
    )

    hl = convert_ll_to_hl(board)
    board2 = convert_hl_to_ll(hl, board.resolution)

    assert len(board2.wires) == 2
    assert board2.wires[0].width == 250
    assert board2.wires[1].width == 150
