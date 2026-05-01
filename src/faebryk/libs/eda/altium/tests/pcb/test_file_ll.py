"""Low-level Altium file/LL roundtrip tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from aaf2.cfb import CompoundFileBinary

from faebryk.libs.eda.altium.convert.pcb.file_ll import (
    PcbDocCodec,
    _decode_property_record,
)
from faebryk.libs.eda.altium.convert.pcb.il_ll import (
    convert_il_to_ll,
    convert_ll_to_il,
)
from faebryk.libs.eda.altium.lib.cfb_writer import CfbWriter
from faebryk.libs.eda.altium.models.pcb.ll import (
    AltiumArc,
    AltiumBoardVertex,
    AltiumComponent,
    AltiumFill,
    AltiumNet,
    AltiumPad,
    AltiumPcbDoc,
    AltiumRegion,
    AltiumRule,
    AltiumText,
    AltiumTrack,
    AltiumVia,
)

_CFB_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_DEMO_ROOT = Path("/home/ip/workspace/atopile_altium_pcb/.local/altium_demos")
_LOCAL_PCBDOC_ROOTS = (
    Path("/home/ip/workspace/atopile_altium_pcb/apps/open-atopile/examples"),
    Path("/home/ip/workspace/atopile_altium_pcb/.local/kicad/qa/data/pcbnew/plugins"),
    Path("/home/ip/workspace/atopile_altium_pcb/.local/AltiumSharp/TestData"),
    Path("/home/ip/workspace/atopile_altium_pcb/.local/altium_demos"),
)
_DEFAULT_BINARY_SIMILARITY_THRESHOLD = 0.90
_BINARY_SIMILARITY_ENV = "ALTIUM_BINARY_SIMILARITY_THRESHOLD"
_STREAM_GROUPS_UNDER_TEST = (
    "Board6",
    "Nets6",
    "Components6",
    "Tracks6",
    "Vias6",
    "Pads6",
    "Rules6",
    "Arcs6",
    "Texts6",
    "Fills6",
    "ShapeBasedRegions6",
)


def _build_ll_fixture() -> AltiumPcbDoc:
    return AltiumPcbDoc(
        nets=[
            AltiumNet(index=0, name=""),
            AltiumNet(index=1, name="GND"),
            AltiumNet(index=2, name="VCC"),
        ],
        components=[
            AltiumComponent(
                index=0,
                designator="U1",
                footprint_name="QFN",
                x=10_000,
                y=20_000,
                rotation=90.0,
                layer=1,
                name_on=True,
                comment_on=False,
            )
        ],
        pads=[
            AltiumPad(
                component=0,
                name="1",
                x=11_000,
                y=19_000,
                top_size_x=1200,
                top_size_y=900,
                mid_size_x=1200,
                mid_size_y=900,
                bot_size_x=1200,
                bot_size_y=900,
                hole_size=500,
                shape=2,
                rotation=45.0,
                net=1,
                layer=1,
                is_tht=True,
                plated=True,
                slot_size=900,
                slot_rotation=90.0,
            )
        ],
        tracks=[
            AltiumTrack(
                layer=1,
                net=1,
                x1=0,
                y1=0,
                x2=5_000,
                y2=5_000,
                width=300,
                component=0,
            )
        ],
        arcs=[
            AltiumArc(
                layer=1,
                net=2,
                component=0,
                center_x=4_000,
                center_y=4_000,
                radius=1_500,
                start_angle=10.0,
                end_angle=180.0,
                width=200,
            )
        ],
        texts=[
            AltiumText(
                layer=33,
                component=0,
                x=15_000,
                y=16_000,
                height=1000,
                rotation=180.0,
                is_mirrored=True,
                stroke_width=100,
                text="REF**",
            )
        ],
        fills=[
            AltiumFill(
                layer=37,
                net=2,
                component=0,
                x1=1_000,
                y1=2_000,
                x2=4_000,
                y2=5_000,
                rotation=30.0,
            )
        ],
        vias=[
            AltiumVia(
                x=25_000,
                y=26_000,
                diameter=1200,
                hole_size=600,
                start_layer=1,
                end_layer=32,
                net=2,
            )
        ],
        regions=[
            AltiumRegion(
                layer=56,
                net=0,
                component=-1,
                outline=[(0, 0), (10_000, 0), (10_000, 10_000), (0, 10_000)],
                holes=[[(2_000, 2_000), (3_000, 2_000), (3_000, 3_000)]],
                is_keepout=True,
                keepout_restrictions=3,
            )
        ],
        board_vertices=[
            AltiumBoardVertex(x=0, y=0),
            AltiumBoardVertex(x=100_000, y=0),
            AltiumBoardVertex(x=100_000, y=50_000),
            AltiumBoardVertex(x=0, y=50_000),
            AltiumBoardVertex(x=0, y=0),
        ],
        rules=[
            AltiumRule(
                kind="Clearance",
                name="Default",
                properties={"SCOPE1EXPRESSION": "All", "GAP": "10mil"},
            )
        ],
        layer_count=4,
        board_thickness=250_000,
    )


def _ll_summary(doc: AltiumPcbDoc) -> dict[str, int]:
    return {
        "nets": len(doc.nets),
        "components": len(doc.components),
        "pads": len(doc.pads),
        "tracks": len(doc.tracks),
        "arcs": len(doc.arcs),
        "texts": len(doc.texts),
        "fills": len(doc.fills),
        "vias": len(doc.vias),
        "regions": len(doc.regions),
        "rules": len(doc.rules),
        "board_vertices": len(doc.board_vertices),
        "layer_count": doc.layer_count,
        "board_thickness": doc.board_thickness,
    }


def _demo_pcbdoc_paths() -> list[Path]:
    if not _DEMO_ROOT.exists():
        return []
    return sorted(
        path
        for path in _DEMO_ROOT.rglob("*")
        if path.is_file() and path.suffix.lower() == ".pcbdoc"
    )


def _local_pcbdoc_paths() -> list[Path]:
    paths: set[Path] = set()
    for root in _LOCAL_PCBDOC_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.suffix.lower() == ".pcbdoc":
                paths.add(path)
    return sorted(paths)


def _binary_similarity_threshold() -> float:
    raw = os.environ.get(_BINARY_SIMILARITY_ENV)
    if raw is None:
        return _DEFAULT_BINARY_SIMILARITY_THRESHOLD
    value = float(raw)
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"{_BINARY_SIMILARITY_ENV} must be within [0, 1], got {value}")
    return value


def _extract_cfb_streams(path: Path) -> dict[str, bytes]:
    streams: dict[str, bytes] = {}
    with path.open("rb") as handle:
        cfb = CompoundFileBinary(handle, mode="rb")
        try:

            def recurse(prefix: str) -> None:
                for name, entry in cfb.listdir_dict(prefix).items():
                    full_path = (
                        f"{prefix.rstrip('/')}/{name}" if prefix != "/" else f"/{name}"
                    )
                    if entry.type == "storage":
                        recurse(full_path)
                        continue
                    streams[full_path.strip("/")] = bytes(
                        cfb.open(full_path, mode="r").read()
                    )

            recurse("/")
        finally:
            cfb.close()
    return streams


def _stream_binary_similarity(lhs: dict[str, bytes], rhs: dict[str, bytes]) -> float:
    keys = set(lhs) | set(rhs)
    if not keys:
        return 1.0
    weighted_similarity = 0.0
    total_weight = 0
    for key in sorted(keys):
        left = lhs.get(key, b"")
        right = rhs.get(key, b"")
        if left == right:
            weighted_similarity += 1.0
            total_weight += 1
            continue
        weight = max(len(left), len(right), 1)
        common = min(len(left), len(right))
        same = sum(a == b for a, b in zip(left[:common], right[:common]))
        weighted_similarity += same / weight
        total_weight += 1
    return weighted_similarity / total_weight


def _assert_binary_similarity(source_path: Path, rewritten_path: Path) -> None:
    threshold = _binary_similarity_threshold()
    source_streams = _extract_cfb_streams(source_path)
    rewritten_streams = _extract_cfb_streams(rewritten_path)
    known_keys = set(PcbDocCodec.encode(_build_ll_fixture()))
    similarity = _stream_binary_similarity(
        {key: source_streams.get(key, b"") for key in known_keys},
        {key: rewritten_streams.get(key, b"") for key in known_keys},
    )
    assert similarity >= threshold, (
        f"binary stream similarity {similarity:.3f} is below configured threshold "
        f"{threshold:.3f} for {source_path.name}"
    )


def _mutate_ll_doc(doc: AltiumPcbDoc) -> str:
    if doc.components:
        doc.components[0].x += 1
        return "component-x"
    if doc.nets:
        doc.nets[0].name += "_rt"
        return "net-name"
    if doc.pads:
        doc.pads[0].x += 1
        return "pad-x"
    doc.layer_count += 1
    return "layer-count"


def _mutate_il_doc(doc) -> str:
    if doc.components:
        doc.components[0].x += 1
        return "component-x"
    if doc.nets:
        doc.nets[0].name += "_rt"
        return "net-name"
    if doc.pad_primitives:
        doc.pad_primitives[0].x += 1
        return "pad-x"
    doc.board.board_thickness += 1
    return "board-thickness"


def _mutate_ll_stream_group(doc: AltiumPcbDoc, group: str) -> None:
    if group == "Board6":
        doc.board_vertices[0].x += 1
    elif group == "Nets6":
        doc.nets[0].name += "_rt"
    elif group == "Components6":
        doc.components[0].x += 1
    elif group == "Tracks6":
        doc.tracks[0].x1 += 1
    elif group == "Vias6":
        doc.vias[0].x += 1
    elif group == "Pads6":
        doc.pads[0].x += 1
    elif group == "Rules6":
        doc.rules[0].name += "_rt"
    elif group == "Arcs6":
        doc.arcs[0].radius += 1
    elif group == "Texts6":
        doc.texts[0].text += "!"
    elif group == "Fills6":
        doc.fills[0].x1 += 1
    elif group == "ShapeBasedRegions6":
        x, y = doc.regions[0].outline[0]
        doc.regions[0].outline[0] = (x + 1, y)
    else:  # pragma: no cover - defensive
        raise KeyError(group)


def _mutate_il_stream_group(doc, group: str) -> None:
    if group == "Board6":
        start = doc.board.outline[0].start
        doc.board.outline[0].start = (start[0] + 1, start[1])
    elif group == "Nets6":
        doc.nets[0].name += "_rt"
    elif group == "Components6":
        doc.components[0].x += 1
    elif group == "Tracks6":
        doc.track_primitives[0].x1 += 1
    elif group == "Vias6":
        doc.via_primitives[0].x += 1
    elif group == "Pads6":
        doc.pad_primitives[0].x += 1
    elif group == "Rules6":
        doc.rules[0].name += "_rt"
    elif group == "Arcs6":
        arc = next(item for item in doc.primitives if item.primitive_kind == "arc")
        arc.radius += 1
    elif group == "Texts6":
        text = next(item for item in doc.primitives if item.primitive_kind == "text")
        text.text += "!"
    elif group == "Fills6":
        fill = next(item for item in doc.primitives if item.primitive_kind == "fill")
        fill.x1 += 1
    elif group == "ShapeBasedRegions6":
        region = next(
            item for item in doc.primitives if item.primitive_kind == "region"
        )
        x, y = region.outline[0]
        region.outline[0] = (x + 1, y)
    else:  # pragma: no cover - defensive
        raise KeyError(group)


def _require_cfb_pcbdoc(path: Path) -> None:
    if path.read_bytes()[:8] != _CFB_MAGIC:
        pytest.skip(f"{path.name} is not a compound-file PcbDoc")


def test_serialize_deserialize_pcbdoc_roundtrips_ll_semantics() -> None:
    doc = _build_ll_fixture()
    streams = PcbDocCodec.encode(doc)

    decoded = PcbDocCodec.decode(streams)
    doc.raw_streams = streams
    doc.semantic_fingerprint = decoded.semantic_fingerprint
    doc.layer_names = decoded.layer_names

    assert decoded == doc


def test_read_pcbdoc_roundtrips_written_cfb_file(tmp_path) -> None:
    doc = _build_ll_fixture()
    output_path = tmp_path / "fixture.PcbDoc"

    writer = CfbWriter()
    streams = PcbDocCodec.encode(doc)
    for stream_path, data in streams.items():
        writer.add_stream(stream_path, data)
    writer.write(output_path)

    decoded = PcbDocCodec.read(output_path)
    doc.raw_streams = streams
    doc.semantic_fingerprint = decoded.semantic_fingerprint
    doc.layer_names = decoded.layer_names

    assert decoded == doc
    assert PcbDocCodec.encode(decoded) == streams


def test_text_record_roundtrips_extended_text_metadata() -> None:
    doc = _build_ll_fixture()
    doc.texts = [
        AltiumText(
            layer=57,
            component=0,
            x=15_000,
            y=16_000,
            height=1000,
            rotation=180.0,
            is_mirrored=True,
            stroke_width=100,
            text=".Designator",
            stroke_font_type=1,
            is_comment=False,
            is_designator=True,
            font_type=1,
            is_bold=True,
            is_italic=True,
            font_name="Arial",
            is_inverted=True,
            is_inverted_rect=True,
            is_frame=True,
            is_offset_border=True,
            is_justification_valid=True,
            margin_border_width=25,
            textbox_rect_width=2000,
            textbox_rect_height=300,
            text_offset_width=50,
            text_justification=7,
        )
    ]

    decoded = PcbDocCodec.decode(PcbDocCodec.encode(doc))

    assert decoded.texts[0] == doc.texts[0]


def test_decode_property_record_accepts_real_world_missing_trailing_nul() -> None:
    payload = b"|RECORD=Component|NAME=C17|SOURCEDESCRIPTION=0603  X5R 1?|"
    record = len(payload).to_bytes(4, "little") + payload

    decoded = _decode_property_record(record)

    assert decoded["RECORD"] == "Component"
    assert decoded["NAME"] == "C17"
    assert decoded["SOURCEDESCRIPTION"] == "0603  X5R 1?"


def test_serialize_pcbdoc_preserves_passthrough_streams() -> None:
    doc = _build_ll_fixture()
    doc.raw_streams = {
        "FileHeaderSix": b"legacy-header",
        "Classes6/Header": b"\x01\x00\x00\x00",
        "Classes6/Data": b"raw-classes",
        "Models/7": b"raw-model-stream",
        "Rules6/Header": b"\x01\x00\x00\x00",
        "Rules6/Data": b"raw-rules",
    }
    doc.rules = []

    streams = PcbDocCodec.encode(doc)

    assert streams["FileHeaderSix"] == b"legacy-header"
    assert streams["Classes6/Header"] == b"\x01\x00\x00\x00"
    assert streams["Classes6/Data"] == b"raw-classes"
    assert streams["Models/7"] == b"raw-model-stream"
    assert streams["Rules6/Header"] == b"\x01\x00\x00\x00"
    assert streams["Rules6/Data"] == b"raw-rules"


@pytest.mark.skipif(not _DEMO_ROOT.exists(), reason="demo PcbDoc corpus not present")
@pytest.mark.parametrize("path", _demo_pcbdoc_paths(), ids=lambda path: path.name)
def test_demo_file_ll_file_roundtrip(path: Path, tmp_path) -> None:
    _require_cfb_pcbdoc(path)

    ll_before = PcbDocCodec.read(path)
    output_path = tmp_path / f"{path.stem}.roundtrip.PcbDoc"

    writer = CfbWriter()
    for stream_path, data in PcbDocCodec.encode(ll_before).items():
        writer.add_stream(stream_path, data)
    writer.write(output_path)

    ll_after = PcbDocCodec.read(output_path)

    assert _ll_summary(ll_after) == _ll_summary(ll_before)
    _assert_binary_similarity(path, output_path)


@pytest.mark.skipif(not _DEMO_ROOT.exists(), reason="demo PcbDoc corpus not present")
@pytest.mark.parametrize("path", _demo_pcbdoc_paths(), ids=lambda path: path.name)
def test_demo_file_ll_il_ll_file_roundtrip(path: Path, tmp_path) -> None:
    _require_cfb_pcbdoc(path)

    ll_before = PcbDocCodec.read(path)
    il = convert_ll_to_il(ll_before)
    ll_mid = convert_il_to_ll(il)
    output_path = tmp_path / f"{path.stem}.il-roundtrip.PcbDoc"

    writer = CfbWriter()
    for stream_path, data in PcbDocCodec.encode(ll_mid).items():
        writer.add_stream(stream_path, data)
    writer.write(output_path)

    ll_after = PcbDocCodec.read(output_path)

    assert _ll_summary(ll_after) == _ll_summary(ll_before)
    _assert_binary_similarity(path, output_path)


@pytest.mark.skipif(
    not any(root.exists() for root in _LOCAL_PCBDOC_ROOTS),
    reason="local PcbDoc corpus not present",
)
@pytest.mark.parametrize("path", _local_pcbdoc_paths(), ids=lambda path: path.name)
def test_local_corpus_file_ll_file_binary_similarity(path: Path, tmp_path) -> None:
    _require_cfb_pcbdoc(path)

    ll_doc = PcbDocCodec.read(path)
    output_path = tmp_path / f"{path.stem}.corpus-roundtrip.PcbDoc"

    writer = CfbWriter()
    for stream_path, data in PcbDocCodec.encode(ll_doc).items():
        writer.add_stream(stream_path, data)
    writer.write(output_path)

    _assert_binary_similarity(path, output_path)


@pytest.mark.skipif(
    not any(root.exists() for root in _LOCAL_PCBDOC_ROOTS),
    reason="local PcbDoc corpus not present",
)
@pytest.mark.parametrize("path", _local_pcbdoc_paths(), ids=lambda path: path.name)
def test_local_corpus_file_ll_il_ll_file_binary_similarity(
    path: Path, tmp_path
) -> None:
    _require_cfb_pcbdoc(path)

    il_doc = convert_ll_to_il(PcbDocCodec.read(path))
    ll_doc = convert_il_to_ll(il_doc)
    output_path = tmp_path / f"{path.stem}.corpus-il-roundtrip.PcbDoc"

    writer = CfbWriter()
    for stream_path, data in PcbDocCodec.encode(ll_doc).items():
        writer.add_stream(stream_path, data)
    writer.write(output_path)

    _assert_binary_similarity(path, output_path)


@pytest.mark.skipif(
    not any(root.exists() for root in _LOCAL_PCBDOC_ROOTS),
    reason="local PcbDoc corpus not present",
)
@pytest.mark.parametrize("path", _local_pcbdoc_paths(), ids=lambda path: path.name)
def test_local_corpus_mutated_ll_roundtrip_keeps_high_similarity(
    path: Path, tmp_path
) -> None:
    _require_cfb_pcbdoc(path)

    ll_doc = PcbDocCodec.read(path)
    _mutate_ll_doc(ll_doc)
    output_path = tmp_path / f"{path.stem}.mutated-ll.PcbDoc"

    writer = CfbWriter()
    for stream_path, data in PcbDocCodec.encode(ll_doc).items():
        writer.add_stream(stream_path, data)
    writer.write(output_path)

    _assert_binary_similarity(path, output_path)


@pytest.mark.skipif(
    not any(root.exists() for root in _LOCAL_PCBDOC_ROOTS),
    reason="local PcbDoc corpus not present",
)
@pytest.mark.parametrize("path", _local_pcbdoc_paths(), ids=lambda path: path.name)
def test_local_corpus_mutated_il_roundtrip_keeps_high_similarity(
    path: Path, tmp_path
) -> None:
    _require_cfb_pcbdoc(path)

    il_doc = convert_ll_to_il(PcbDocCodec.read(path))
    _mutate_il_doc(il_doc)
    ll_doc = convert_il_to_ll(il_doc)
    output_path = tmp_path / f"{path.stem}.mutated-il.PcbDoc"

    writer = CfbWriter()
    for stream_path, data in PcbDocCodec.encode(ll_doc).items():
        writer.add_stream(stream_path, data)
    writer.write(output_path)

    _assert_binary_similarity(path, output_path)


@pytest.mark.parametrize("group", _STREAM_GROUPS_UNDER_TEST)
def test_fixture_each_ll_stream_mutation_stays_high_similarity(
    group: str, tmp_path
) -> None:
    baseline = _build_ll_fixture()
    baseline_path = tmp_path / "baseline-ll.PcbDoc"
    writer = CfbWriter()
    for stream_path, data in PcbDocCodec.encode(baseline).items():
        writer.add_stream(stream_path, data)
    writer.write(baseline_path)

    mutated = PcbDocCodec.read(baseline_path)
    _mutate_ll_stream_group(mutated, group)
    mutated_path = tmp_path / f"{group}-ll.PcbDoc"

    writer = CfbWriter()
    for stream_path, data in PcbDocCodec.encode(mutated).items():
        writer.add_stream(stream_path, data)
    writer.write(mutated_path)

    similarity = _stream_binary_similarity(
        _extract_cfb_streams(baseline_path),
        _extract_cfb_streams(mutated_path),
    )
    assert similarity >= 0.98, f"{group} similarity={similarity:.3f}"


@pytest.mark.parametrize("group", _STREAM_GROUPS_UNDER_TEST)
def test_fixture_each_il_stream_mutation_stays_high_similarity(
    group: str, tmp_path
) -> None:
    baseline = _build_ll_fixture()
    baseline_path = tmp_path / "baseline-il.PcbDoc"
    writer = CfbWriter()
    for stream_path, data in PcbDocCodec.encode(baseline).items():
        writer.add_stream(stream_path, data)
    writer.write(baseline_path)

    mutated = convert_ll_to_il(PcbDocCodec.read(baseline_path))
    _mutate_il_stream_group(mutated, group)
    mutated_path = tmp_path / f"{group}-il.PcbDoc"

    writer = CfbWriter()
    for stream_path, data in PcbDocCodec.encode(convert_il_to_ll(mutated)).items():
        writer.add_stream(stream_path, data)
    writer.write(mutated_path)

    similarity = _stream_binary_similarity(
        _extract_cfb_streams(baseline_path),
        _extract_cfb_streams(mutated_path),
    )
    assert similarity >= 0.95, f"{group} similarity={similarity:.3f}"
