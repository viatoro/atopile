"""KiCad <-> Altium IL conversion tests."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from faebryk.libs.eda.altium.convert.pcb import il_kicad
from faebryk.libs.eda.altium.convert.pcb.file_ll import PcbDocCodec
from faebryk.libs.eda.altium.convert.pcb.il_ll import convert_ll_to_il
from faebryk.libs.eda.altium.models.constants import mm_to_altium
from faebryk.libs.eda.altium.models.pcb.il import (
    AltiumComponent,
    AltiumFill,
    AltiumLayerType,
    AltiumNet,
    AltiumPad,
    AltiumPadShape,
    AltiumPcb,
    AltiumPolygonConnectStyle,
    AltiumPrimitiveKind,
    AltiumRegion,
    AltiumRuleClearance,
    AltiumRuleHoleSize,
    AltiumRulePolygonConnectStyle,
    AltiumRuleWidth,
    AltiumText,
    AltiumTrack,
    AltiumVia,
    BoardConfig,
    BoardCopperOrdering,
    BoardLayer,
    BoardOutlineSegment,
)
from faebryk.libs.kicad.fileformats import kicad

_CFB_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_KICAD_CLI = shutil.which("kicad-cli")
_LOCAL_PCBDOC_ROOTS = (
    Path("/home/ip/workspace/atopile_altium_pcb/apps/open-atopile/examples"),
    Path("/home/ip/workspace/atopile_altium_pcb/.local/kicad/qa/data/pcbnew/plugins"),
    Path("/home/ip/workspace/atopile_altium_pcb/.local/AltiumSharp/TestData"),
    Path("/home/ip/workspace/atopile_altium_pcb/.local/altium_demos"),
)
_LUMINOX_PCBDOC = Path(
    "/home/ip/workspace/atopile_altium_pcb/.local/altium_demos/"
    "hardware-luminox_to_sdi12/main.PcbDoc"
)
_LUMINOX_KICAD_IMPORT = Path(
    "/home/ip/workspace/atopile_altium_pcb/.local/altium_demos/"
    "hardware-luminox_to_sdi12/main.kicad.kicad_pcb"
)


def _local_pcbdoc_paths() -> list[Path]:
    paths: list[Path] = []
    for root in _LOCAL_PCBDOC_ROOTS:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if (
                path.is_file()
                and path.suffix.lower() == ".pcbdoc"
                and not path.name.endswith(".out.PcbDoc")
            ):
                paths.append(path)
    return paths


def _require_cfb_pcbdoc(path: Path) -> None:
    if path.read_bytes()[: len(_CFB_MAGIC)] != _CFB_MAGIC:
        pytest.skip(f"{path.name} is not a CFB `.PcbDoc` fixture")


def _assert_kicad_cli_loads(board_path: Path, tmp_path: Path) -> None:
    if _KICAD_CLI is None:
        pytest.skip("kicad-cli is not installed")
    output_path = tmp_path / f"{board_path.stem}.d356"
    completed = subprocess.run(
        [
            _KICAD_CLI,
            "pcb",
            "export",
            "ipcd356",
            str(board_path),
            "-o",
            str(output_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, (
        f"kicad-cli failed to load {board_path.name}\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )
    assert output_path.exists()


def _properties_by_name(footprint) -> dict[str, object]:
    return {prop.name: prop for prop in footprint.propertys}


def _xy(x: float, y: float, r: float | None = None) -> SimpleNamespace:
    return SimpleNamespace(x=x, y=y, r=r)


def _font(*, width: float, height: float, thickness: float) -> SimpleNamespace:
    return SimpleNamespace(
        size=SimpleNamespace(w=width, h=height),
        thickness=thickness,
    )


def _effects(
    *, width: float = 1.0, height: float = 1.0, thickness: float = 0.15
) -> SimpleNamespace:
    return SimpleNamespace(
        hide=False,
        font=_font(width=width, height=height, thickness=thickness),
    )


def _build_minimal_kicad_pcb() -> SimpleNamespace:
    footprint = SimpleNamespace(
        propertys=[
            SimpleNamespace(
                name="Reference",
                value="U1",
                hide=False,
                effects=_effects(width=1.0, height=1.2, thickness=0.12),
                layer="F.SilkS",
                at=_xy(0.0, -2.0, 0.0),
            ),
            SimpleNamespace(
                name="Value",
                value="SOIC-8",
                hide=False,
                effects=_effects(width=1.2, height=1.2, thickness=0.12),
                layer="F.SilkS",
                at=_xy(0.0, 2.0, 0.0),
            ),
        ],
        layer="F.Cu",
        name="SOIC-8",
        at=_xy(10.0, 20.0, 90.0),
        pads=[
            SimpleNamespace(
                name="1",
                at=_xy(1.0, 2.0, 45.0),
                size=SimpleNamespace(w=1.2, h=1.0),
                type="thru_hole",
                drill=SimpleNamespace(size_x=0.6, size_y=0.6),
                shape="rect",
                net=SimpleNamespace(number=1),
                layers=["F.Cu", "B.Cu"],
            )
        ],
        fp_lines=[
            SimpleNamespace(
                layer="F.SilkS",
                start=_xy(-1.0, -1.0),
                end=_xy(1.0, -1.0),
                stroke=SimpleNamespace(width=0.12),
            )
        ],
        fp_arcs=[],
        fp_circles=[],
        fp_rects=[],
        fp_texts=[],
        fp_poly=[],
    )
    return SimpleNamespace(
        nets=[
            SimpleNamespace(number=0, name=""),
            SimpleNamespace(number=1, name="GND"),
        ],
        footprints=[footprint],
        segments=[
            SimpleNamespace(
                layer="F.Cu",
                net=1,
                start=_xy(0.0, 0.0),
                end=_xy(5.0, 0.0),
                width=0.25,
            )
        ],
        vias=[
            SimpleNamespace(
                at=_xy(6.0, 6.0),
                size=0.8,
                drill=0.3,
                layers=["F.Cu", "B.Cu"],
                net=1,
            )
        ],
        gr_lines=[],
        gr_arcs=[],
        gr_circles=[],
        gr_rects=[
            SimpleNamespace(
                layer="Edge.Cuts",
                start=_xy(0.0, 0.0),
                end=_xy(20.0, 10.0),
                stroke=SimpleNamespace(width=0.1),
                fill=None,
            )
        ],
        gr_polys=[],
        gr_texts=[],
        zones=[],
        setup=SimpleNamespace(
            rules=SimpleNamespace(
                min_clearance=0.2,
                min_track_width=0.25,
                min_through_hole_diameter=0.5,
            )
        ),
        general=SimpleNamespace(thickness=1.6),
        layers=[
            SimpleNamespace(name="F.Cu", type="signal"),
            SimpleNamespace(name="B.Cu", type="signal"),
            SimpleNamespace(name="F.SilkS", type="user"),
        ],
    )


def _build_supported_altium_fixture() -> AltiumPcb:
    return AltiumPcb(
        board=BoardConfig(
            name="bidirectional-fixture",
            board_thickness=mm_to_altium(1.6),
            layers=[
                BoardLayer(
                    id="layer-top",
                    name="Top Layer",
                    kind=AltiumLayerType.COPPER,
                    altium_layer_number=1,
                ),
                BoardLayer(
                    id="layer-bottom",
                    name="Bottom Layer",
                    kind=AltiumLayerType.COPPER,
                    altium_layer_number=32,
                ),
                BoardLayer(
                    id="layer-overlay",
                    name="Top Overlay",
                    kind=AltiumLayerType.OVERLAY,
                    altium_layer_number=33,
                ),
            ],
            copper_ordering=BoardCopperOrdering(
                ordered_layer_ids=["layer-top", "layer-bottom"]
            ),
            outline=[
                BoardOutlineSegment(start=(0, 0), end=(500_000, 0)),
                BoardOutlineSegment(start=(500_000, 0), end=(500_000, 300_000)),
                BoardOutlineSegment(start=(500_000, 300_000), end=(0, 300_000)),
                BoardOutlineSegment(start=(0, 300_000), end=(0, 0)),
            ],
            extra_properties={"ll_layer_count": 2},
        ),
        nets=[AltiumNet(id="net-1", name="GND")],
        components=[
            AltiumComponent(
                id="component-1",
                designator="U1",
                footprint="SOIC-8",
                x=100_000,
                y=200_000,
                rotation=90.0,
                layer=1,
                name_on=False,
                comment_on=False,
            )
        ],
        rules=[
            AltiumRuleClearance(name="Clearance", gap=mm_to_altium(0.2)),
            AltiumRuleWidth(
                name="Width",
                min_limit=mm_to_altium(0.25),
                max_limit=mm_to_altium(0.25),
                preferred=mm_to_altium(0.25),
            ),
            AltiumRuleHoleSize(
                name="HoleSize",
                min_limit=mm_to_altium(0.5),
                max_limit=mm_to_altium(0.5),
            ),
            AltiumRulePolygonConnectStyle(
                name="PolygonConnect",
                connect_style=AltiumPolygonConnectStyle.RELIEF,
                air_gap_width=mm_to_altium(0.2),
                relief_conductor_width=mm_to_altium(0.25),
            ),
        ],
        primitives=[
            AltiumPad(
                id="pad-1",
                component_id="component-1",
                name="1",
                layer=1,
                net_id="net-1",
                x=120_000,
                y=180_000,
                top_size_x=120_000,
                top_size_y=100_000,
                mid_size_x=120_000,
                mid_size_y=100_000,
                bot_size_x=120_000,
                bot_size_y=100_000,
                hole_size=60_000,
                shape=AltiumPadShape.RECT,
                rotation=135.0,
                is_tht=True,
            ),
            AltiumTrack(
                id="track-1",
                layer=1,
                net_id="net-1",
                x1=0,
                y1=0,
                x2=500_000,
                y2=0,
                width=25_000,
            ),
            AltiumVia(
                id="via-1",
                net_id="net-1",
                x=250_000,
                y=150_000,
                diameter=80_000,
                hole_size=30_000,
                start_layer=1,
                end_layer=32,
            ),
            AltiumText(
                id="text-ref",
                component_id="component-1",
                layer=33,
                text="U1",
                x=100_000,
                y=220_000,
                height=mm_to_altium(0.8),
                rotation=90.0,
                stroke_width=mm_to_altium(0.1),
            ),
            AltiumText(
                id="text-value",
                component_id="component-1",
                layer=33,
                text="10k",
                x=100_000,
                y=240_000,
                height=mm_to_altium(0.6),
                rotation=90.0,
                stroke_width=mm_to_altium(0.08),
            ),
            AltiumFill(
                id="fill-1",
                layer=33,
                x1=10_000,
                y1=10_000,
                x2=60_000,
                y2=40_000,
            ),
            AltiumRegion(
                id="region-1",
                layer=1,
                net_id="net-1",
                outline=[
                    (50_000, 50_000),
                    (150_000, 50_000),
                    (150_000, 150_000),
                    (50_000, 150_000),
                ],
            ),
        ],
    )


def test_convert_pcb_builds_altium_il_directly() -> None:
    pcb = il_kicad.convert_pcb(_build_minimal_kicad_pcb())

    assert [net.name for net in pcb.nets] == ["GND"]
    assert pcb.components[0].id == "component-1"
    assert pcb.components[0].designator == "U1"
    assert pcb.board.extra_properties["ll_layer_count"] == 2
    assert pcb.board.copper_ordering.ordered_layer_ids == ["layer-1", "layer-32"]
    assert [layer.altium_layer_number for layer in pcb.board.layers] == [1, 32, 33]
    assert len(pcb.board.outline) == 4

    pads = pcb.pad_primitives
    assert len(pads) == 1
    assert pads[0].component_id == "component-1"
    assert pads[0].net_id == "net-1"
    assert pads[0].shape.value == "rect"

    assert len(pcb.track_primitives) == 2
    assert len(pcb.via_primitives) == 1
    text_primitives = [
        primitive
        for primitive in pcb.primitives
        if primitive.primitive_kind == AltiumPrimitiveKind.TEXT
    ]
    assert len(text_primitives) == 2

    assert isinstance(pcb.rules[0], AltiumRuleClearance)
    assert isinstance(pcb.rules[1], AltiumRuleWidth)
    assert isinstance(pcb.rules[2], AltiumRuleHoleSize)


def test_convert_altium_to_kicad_builds_kicad_pcb() -> None:
    pcb = il_kicad.convert_altium_to_kicad(_build_supported_altium_fixture())

    assert len(pcb.nets) == 2
    assert len(pcb.footprints) == 1
    assert len(pcb.segments) == 1
    assert len(pcb.vias) == 1
    assert len(pcb.gr_rects) == 1
    assert len(pcb.zones) == 1
    assert pcb.footprints[0].pads[0].name == "1"
    assert pcb.footprints[0].pads[0].at.r == 135.0
    assert any(layer.name == "Edge.Cuts" for layer in pcb.layers)
    assert pcb.nets[0].number == 0
    assert pcb.nets[0].name == ""
    assert pcb.zones[0].net_name == "GND"
    assert pcb.zones[0].connect_pads is not None
    assert pcb.zones[0].connect_pads.clearance == pytest.approx(0.2, abs=1e-3)
    assert pcb.zones[0].fill is not None
    assert pcb.zones[0].fill.thermal_gap == pytest.approx(0.2, abs=1e-3)
    assert pcb.zones[0].fill.thermal_bridge_width == pytest.approx(0.25, abs=1e-3)
    assert pcb.setup.rules is None

    properties = {prop.name: prop for prop in pcb.footprints[0].propertys}
    assert properties["Reference"].value == "U1"
    assert properties["Reference"].hide is True
    assert properties["Reference"].layer == "F.SilkS"
    assert properties["Reference"].at.r == 90.0
    assert properties["Reference"].effects.font.size.h == pytest.approx(0.8, abs=1e-3)
    assert properties["Value"].value == "10k"
    assert properties["Value"].hide is True
    assert properties["Value"].layer == "F.SilkS"
    assert properties["Value"].effects.font.size.h == pytest.approx(0.6, abs=1e-3)
    assert pcb.footprints[0].fp_texts == []

    serialized = kicad.dumps(kicad.pcb.PcbFile(kicad_pcb=pcb))
    assert "(kicad_pcb" in serialized


def test_convert_altium_to_kicad_uses_actual_copper_layers_not_ll_hint() -> None:
    fixture = _build_supported_altium_fixture()
    fixture.board.extra_properties["ll_layer_count"] = 6

    pcb = il_kicad.convert_altium_to_kicad(fixture)

    copper_layer_names = [layer.name for layer in pcb.layers if layer.type == "signal"]
    assert copper_layer_names == ["F.Cu", "B.Cu"]


def test_convert_altium_to_kicad_rounds_odd_used_copper_layers_up_to_even() -> None:
    fixture = _build_supported_altium_fixture()
    fixture.board.layers.insert(
        1,
        BoardLayer(
            id="layer-mid1",
            name="Mid1",
            kind=AltiumLayerType.COPPER,
            altium_layer_number=2,
        ),
    )
    fixture.board.copper_ordering = BoardCopperOrdering(
        ordered_layer_ids=["layer-top", "layer-mid1", "layer-bottom"]
    )

    pcb = il_kicad.convert_altium_to_kicad(fixture)

    copper_layer_names = [layer.name for layer in pcb.layers if layer.type == "signal"]
    assert copper_layer_names == ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]


def test_supported_il_subset_roundtrips_through_kicad_bridge() -> None:
    original = _build_supported_altium_fixture()
    pcb = il_kicad.convert_altium_to_kicad(original)
    serialized = kicad.dumps(kicad.pcb.PcbFile(kicad_pcb=pcb))
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "roundtrip.kicad_pcb"
        path.write_text(serialized, encoding="utf-8")
        reparsed = kicad.loads(kicad.pcb.PcbFile, path).kicad_pcb

        roundtripped = il_kicad.convert_kicad_to_altium(reparsed)

        assert [net.name for net in roundtripped.nets] == ["GND"]
        assert roundtripped.components[0].designator == "U1"
        assert roundtripped.pad_primitives[0].name == "1"
        assert roundtripped.pad_primitives[0].shape == AltiumPadShape.RECT
        assert len(roundtripped.track_primitives) == 1
        assert len(roundtripped.via_primitives) == 1
        assert any(
            isinstance(primitive, AltiumFill) for primitive in roundtripped.primitives
        )
        assert any(
            isinstance(primitive, AltiumRegion) and primitive.net_id == "net-1"
            for primitive in roundtripped.primitives
        )


def test_supported_il_subset_serializes_to_kicad_cli_loadable_board(tmp_path) -> None:
    pcb = il_kicad.convert_altium_to_kicad(_build_supported_altium_fixture())
    board_path = tmp_path / "supported-fixture.kicad_pcb"
    kicad.dumps(kicad.pcb.PcbFile(kicad_pcb=pcb), board_path)

    _assert_kicad_cli_loads(board_path, tmp_path)


def test_slotted_pad_drill_emits_kicad_oval_drill() -> None:
    doc = _build_supported_altium_fixture()
    doc.primitives.append(
        AltiumPad(
            id="pad-slot",
            component_id="component-1",
            name="2",
            layer=1,
            x=140_000,
            y=180_000,
            top_size_x=140_000,
            top_size_y=80_000,
            mid_size_x=140_000,
            mid_size_y=80_000,
            bot_size_x=140_000,
            bot_size_y=80_000,
            hole_size=mm_to_altium(0.4),
            slot_size=mm_to_altium(1.0),
            slot_rotation=90.0,
            shape=AltiumPadShape.RECT,
            rotation=90.0,
            is_tht=True,
        )
    )

    pcb = il_kicad.convert_altium_to_kicad(doc)
    slot_pad = next(pad for pad in pcb.footprints[0].pads if pad.name == "2")

    assert slot_pad.drill is not None
    assert slot_pad.drill.shape == "oval"
    assert slot_pad.drill.size_x == pytest.approx(0.4, abs=1e-3)
    assert slot_pad.drill.size_y == pytest.approx(1.0, abs=1e-3)


def test_component_placeholder_text_maps_to_kicad_placeholder_user_text() -> None:
    doc = _build_supported_altium_fixture()
    doc.components[0].name_on = True
    doc.components[0].comment_on = True
    doc.primitives = [
        primitive
        for primitive in doc.primitives
        if not isinstance(primitive, AltiumText)
    ]
    doc.primitives.extend(
        [
            AltiumText(
                id="text-reference",
                component_id="component-1",
                layer=33,
                text="U1",
                x=100_000,
                y=220_000,
                height=mm_to_altium(0.8),
                rotation=90.0,
                stroke_width=mm_to_altium(0.1),
            ),
            AltiumText(
                id="text-value",
                component_id="component-1",
                layer=35,
                text="TPS71550DCKR",
                x=100_000,
                y=240_000,
                height=mm_to_altium(0.6),
                rotation=90.0,
                stroke_width=mm_to_altium(0.08),
            ),
            AltiumText(
                id="text-placeholder",
                component_id="component-1",
                layer=33,
                text=".Designator",
                x=120_000,
                y=260_000,
                height=mm_to_altium(0.5),
                rotation=0.0,
                stroke_width=mm_to_altium(0.08),
            ),
        ]
    )

    pcb = il_kicad.convert_altium_to_kicad(doc)

    properties = {prop.name: prop for prop in pcb.footprints[0].propertys}
    assert properties["Reference"].hide is False
    assert properties["Reference"].value == "U1"
    assert properties["Reference"].effects.font.size.h == pytest.approx(0.8, abs=1e-3)
    assert properties["Value"].hide is False
    assert properties["Value"].value == "TPS71550DCKR"
    assert properties["Value"].layer == "F.Paste"
    assert properties["Value"].effects.font.size.h == pytest.approx(0.6, abs=1e-3)
    assert [text.text for text in pcb.footprints[0].fp_texts] == ["${REFERENCE}"]


def test_netless_copper_region_emits_kicad_zone() -> None:
    doc = _build_supported_altium_fixture()
    doc.primitives.append(
        AltiumRegion(
            id="region-2",
            layer=32,
            outline=[
                (200_000, 50_000),
                (300_000, 50_000),
                (300_000, 150_000),
                (200_000, 150_000),
            ],
        )
    )

    pcb = il_kicad.convert_altium_to_kicad(doc)

    assert len(pcb.zones) == 2
    netless_zone = next(zone for zone in pcb.zones if zone.net_name == "")
    assert netless_zone.layer == "B.Cu"
    assert netless_zone.net == 0


@pytest.mark.skipif(_KICAD_CLI is None, reason="kicad-cli is not installed")
@pytest.mark.skipif(
    not any(root.exists() for root in _LOCAL_PCBDOC_ROOTS),
    reason="local PcbDoc corpus not present",
)
@pytest.mark.parametrize("path", _local_pcbdoc_paths(), ids=lambda path: path.name)
def test_local_pcbdoc_corpus_converts_to_kicad_cli_loadable_board(
    path: Path, tmp_path
) -> None:
    _require_cfb_pcbdoc(path)

    il_doc = convert_ll_to_il(PcbDocCodec.read(path))
    kicad_pcb = il_kicad.convert_altium_to_kicad(il_doc)
    board_path = tmp_path / f"{path.stem}.kicad_pcb"
    kicad.dumps(kicad.pcb.PcbFile(kicad_pcb=kicad_pcb), board_path)

    _assert_kicad_cli_loads(board_path, tmp_path)


@pytest.mark.skipif(
    not _LUMINOX_PCBDOC.exists(),
    reason="luminox demo board not present",
)
def test_luminox_demo_preserves_fixed_bridge_behaviour() -> None:
    il_doc = convert_ll_to_il(PcbDocCodec.read(_LUMINOX_PCBDOC))
    pcb = il_kicad.convert_altium_to_kicad(il_doc)

    assert len(pcb.nets) == 19
    assert pcb.general is not None
    assert pcb.general.thickness == pytest.approx(0.41116, abs=0.01)
    assert len(pcb.zones) == 2
    assert {zone.net_name for zone in pcb.zones} == {"GND"}
    assert sum(len(footprint.fp_texts) for footprint in pcb.footprints) == 6

    footprints_by_ref = {
        _properties_by_name(footprint)["Reference"].value: footprint
        for footprint in pcb.footprints
    }

    c6 = footprints_by_ref["C6"]
    c6_props = _properties_by_name(c6)
    assert c6_props["Value"].value == "220pF"
    assert list(c6.fp_texts) == []

    u2 = footprints_by_ref["U2"]
    assert [text.text for text in list(u2.fp_texts)] == ["${REFERENCE}"]
    assert u2.fp_texts[0].layer.layer == "User.1"

    icsp = footprints_by_ref["ICSP"]
    icsp_props = _properties_by_name(icsp)
    assert icsp_props["Value"].value == "ICSP"
    assert {text.text for text in list(icsp.fp_texts)} == {"1", "2", "5", "6"}

    j1 = footprints_by_ref["J1"]
    assert list(j1.fp_texts) == []


@pytest.mark.skipif(
    not (_LUMINOX_PCBDOC.exists() and _LUMINOX_KICAD_IMPORT.exists()),
    reason="luminox demo comparison boards not present",
)
def test_luminox_demo_text_matches_kicad_import_reference() -> None:
    il_doc = convert_ll_to_il(PcbDocCodec.read(_LUMINOX_PCBDOC))
    pcb = il_kicad.convert_altium_to_kicad(il_doc)
    reference = kicad.loads(kicad.pcb.PcbFile, _LUMINOX_KICAD_IMPORT).kicad_pcb

    def _footprints_by_ref(board) -> dict[str, object]:
        return {
            _properties_by_name(footprint)["Reference"].value: footprint
            for footprint in board.footprints
        }

    def _assert_text_matches(ours, expected) -> None:
        ours_layer = ours.layer.layer if hasattr(ours.layer, "layer") else ours.layer
        expected_layer = (
            expected.layer.layer if hasattr(expected.layer, "layer") else expected.layer
        )
        assert ours_layer == expected_layer
        assert ours.at.x == pytest.approx(expected.at.x, abs=1e-3)
        assert ours.at.y == pytest.approx(expected.at.y, abs=1e-3)
        assert ours.at.r == pytest.approx(expected.at.r, abs=1e-3)
        assert ours.effects.font.size.h == pytest.approx(
            expected.effects.font.size.h,
            abs=1e-3,
        )

    ours_fps = _footprints_by_ref(pcb)
    ref_fps = _footprints_by_ref(reference)

    u2_props = _properties_by_name(ours_fps["U2"])
    ref_u2_props = _properties_by_name(ref_fps["U2"])
    _assert_text_matches(u2_props["Reference"], ref_u2_props["Reference"])
    _assert_text_matches(u2_props["Value"], ref_u2_props["Value"])

    c6_props = _properties_by_name(ours_fps["C6"])
    ref_c6_props = _properties_by_name(ref_fps["C6"])
    _assert_text_matches(c6_props["Reference"], ref_c6_props["Reference"])
    _assert_text_matches(c6_props["Value"], ref_c6_props["Value"])

    y1_text = list(ours_fps["Y1"].fp_texts)[0]
    ref_y1_text = list(ref_fps["Y1"].fp_texts)[0]
    assert y1_text.text == ref_y1_text.text
    _assert_text_matches(y1_text, ref_y1_text)
