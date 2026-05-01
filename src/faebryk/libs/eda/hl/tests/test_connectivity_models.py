"""Prototype HL connectivity tests."""

from __future__ import annotations

from faebryk.libs.eda.hl.convert.pcb_netlist import convert_pcb_to_netlist
from faebryk.libs.eda.hl.convert.schematic_netlist import (
    convert_schematic_to_netlist,
)
from faebryk.libs.eda.hl.models.pcb import (
    PCB,
    Circle,
    Collection,
    ConductiveGeometry,
    LayerID,
    NetID,
    Obround,
    Polygon,
    Rectangle,
    Segment,
    SourceID,
)
from faebryk.libs.eda.hl.models.pcb import (
    Point2D as PcbPoint2D,
)
from faebryk.libs.eda.hl.models.schematic import (
    Net,
    Pin,
    Schematic,
    Sheet,
    Symbol,
    WireSegment,
)

_QUESTION_MARK_POLICY = {
    "numeric_suffix_canonicalization": "terminal_alpha_suffix",
    "single_pin_nc_placeholder": "question_mark",
}


def _point(x: float, y: float) -> PcbPoint2D:
    return PcbPoint2D(x=x, y=y)


def _layer(name: str) -> LayerID:
    return LayerID(name=name)


def _net(name: str | None) -> NetID | None:
    return NetID(name=name) if name is not None else None


def _pad(
    *,
    component_id: str,
    pad_name: str,
    x: float,
    y: float,
    layers: list[str],
    net_name: str | None = None,
) -> Collection:
    return Collection(
        id=SourceID(id=f"{component_id}:{pad_name}"),
        extra_properties={"terminal_id": pad_name},
        geometries=[
            ConductiveGeometry(
                shape=Circle(center=_point(0, 0), radius=0.6),
                location=_point(x, y),
                layers=[_layer(layer) for layer in layers],
                net=_net(net_name),
            )
        ],
    )


def _rect_pad(
    *,
    component_id: str,
    pad_name: str,
    x: float,
    y: float,
    width: float,
    height: float,
    layers: list[str],
    rotation_deg: float = 0.0,
    net_name: str | None = None,
) -> Collection:
    return Collection(
        id=SourceID(id=f"{component_id}:{pad_name}"),
        extra_properties={"terminal_id": pad_name},
        geometries=[
            ConductiveGeometry(
                shape=Rectangle(
                    center=_point(0, 0),
                    width=width,
                    height=height,
                    rotation_deg=rotation_deg,
                ),
                location=_point(x, y),
                layers=[_layer(layer) for layer in layers],
                net=_net(net_name),
            )
        ],
    )


def _obround_pad(
    *,
    component_id: str,
    pad_name: str,
    x: float,
    y: float,
    width: float,
    height: float,
    layers: list[str],
    rotation_deg: float = 0.0,
    net_name: str | None = None,
) -> Collection:
    return Collection(
        id=SourceID(id=f"{component_id}:{pad_name}"),
        extra_properties={"terminal_id": pad_name},
        geometries=[
            ConductiveGeometry(
                shape=Obround(
                    center=_point(0, 0),
                    width=width,
                    height=height,
                    rotation_deg=rotation_deg,
                ),
                location=_point(x, y),
                layers=[_layer(layer) for layer in layers],
                net=_net(net_name),
            )
        ],
    )


def _component(
    *,
    component_id: str,
    refdes: str,
    pads: list[Collection],
) -> Collection:
    return Collection(
        id=SourceID(id=component_id),
        extra_properties={"refdes": refdes},
        collections=pads,
    )


def _track(
    *,
    start: tuple[float, float],
    end: tuple[float, float],
    layer: str,
    net_name: str | None = None,
) -> ConductiveGeometry:
    return ConductiveGeometry(
        shape=Segment(start=_point(*start), end=_point(*end)),
        location=_point(0, 0),
        layers=[_layer(layer)],
        net=_net(net_name),
    )


def _via(
    *,
    x: float,
    y: float,
    layers: list[str],
    net_name: str | None = None,
) -> ConductiveGeometry:
    return ConductiveGeometry(
        shape=Circle(center=_point(0, 0), radius=0.6),
        location=_point(x, y),
        layers=[_layer(layer) for layer in layers],
        net=_net(net_name),
    )


def _region(
    *,
    vertices: list[tuple[float, float]],
    layer: str,
    net_name: str | None = None,
) -> ConductiveGeometry:
    return ConductiveGeometry(
        shape=Polygon(vertices=[_point(x, y) for x, y in vertices]),
        location=_point(0, 0),
        layers=[_layer(layer)],
        net=_net(net_name),
    )


def test_schematic_hl_reconstructs_named_nets() -> None:
    schematic = Schematic(
        top_sheet_id="top",
        sheets=[
            Sheet(
                id="top",
                symbols=[
                    Symbol(
                        id="u1",
                        refdes="U1",
                        pins=[
                            Pin(name="1", location=(0, 0)),
                            Pin(name="2", location=(0, 10)),
                        ],
                    ),
                    Symbol(
                        id="r1",
                        refdes="R1",
                        pins=[
                            Pin(name="1", location=(20, 0)),
                            Pin(name="2", location=(40, 10)),
                        ],
                    ),
                ],
                wires=[
                    WireSegment(points=[(0, 0), (20, 0)]),
                    WireSegment(points=[(0, 10), (10, 10)]),
                    WireSegment(points=[(30, 10), (40, 10)]),
                ],
                nets=[
                    Net(name="SIG", anchor=(10, 0)),
                    Net(name="VCC", anchor=(10, 10), is_power=True),
                    Net(name="VCC", anchor=(30, 10), is_power=True),
                ],
            )
        ],
    )

    assert convert_schematic_to_netlist(schematic).normalized() == (
        (
            "SIG",
            (
                ("schematic_pin", "r1", "R1", "1"),
                ("schematic_pin", "u1", "U1", "1"),
            ),
        ),
        (
            "VCC",
            (
                ("schematic_pin", "r1", "R1", "2"),
                ("schematic_pin", "u1", "U1", "2"),
            ),
        ),
    )


def test_schematic_hl_synthesizes_deterministic_names_for_unnamed_nets() -> None:
    schematic = Schematic(
        top_sheet_id="top",
        sheets=[
            Sheet(
                id="top",
                symbols=[
                    Symbol(
                        id="c1",
                        refdes="C1",
                        pins=[Pin(name="1", location=(0, 0))],
                    ),
                    Symbol(
                        id="u1",
                        refdes="U1",
                        pins=[Pin(name="8", location=(20, 0))],
                    ),
                ],
                wires=[WireSegment(points=[(0, 0), (20, 0)])],
            )
        ],
    )

    assert convert_schematic_to_netlist(schematic).normalized() == (
        (
            "NetC1_1",
            (
                ("schematic_pin", "c1", "C1", "1"),
                ("schematic_pin", "u1", "U1", "8"),
            ),
        ),
    )


def test_altium_schematic_prefers_more_specific_names_and_drops_trivial_aliases() -> (
    None
):
    schematic = Schematic(
        top_sheet_id="top",
        extra_properties={"netlist_naming_policy": dict(_QUESTION_MARK_POLICY)},
        sheets=[
            Sheet(
                id="top",
                symbols=[
                    Symbol(
                        id="j1",
                        refdes="J1",
                        pins=[Pin(name="1", location=(0, 0))],
                    ),
                    Symbol(
                        id="u1",
                        refdes="U1",
                        pins=[Pin(name="1", location=(20, 0))],
                    ),
                    Symbol(
                        id="j2",
                        refdes="J2",
                        pins=[Pin(name="1", location=(0, 20))],
                    ),
                    Symbol(
                        id="u2",
                        refdes="U2",
                        pins=[Pin(name="1", location=(20, 20))],
                    ),
                ],
                wires=[
                    WireSegment(points=[(0, 0), (20, 0)]),
                    WireSegment(points=[(0, 20), (20, 20)]),
                ],
                nets=[
                    Net(name="VPWR", anchor=(10, 0), is_power=True),
                    Net(name="VPWR_IN", anchor=(10, 0), is_global=False),
                    Net(name="MOT_SD_MODE", anchor=(10, 20), is_global=False),
                    Net(name="SD_MODE", anchor=(10, 20), is_global=False),
                ],
            )
        ],
    )

    assert convert_schematic_to_netlist(schematic).normalized() == (
        (
            "MOT_SD_MODE",
            (
                ("schematic_pin", "j2", "J2", "1"),
                ("schematic_pin", "u2", "U2", "1"),
            ),
        ),
        (
            "VPWR_IN",
            (
                ("schematic_pin", "j1", "J1", "1"),
                ("schematic_pin", "u1", "U1", "1"),
            ),
        ),
    )


def test_altium_schematic_canonicalizes_single_suffix_repeated_numeric_names() -> None:
    schematic = Schematic(
        top_sheet_id="top",
        extra_properties={"netlist_naming_policy": dict(_QUESTION_MARK_POLICY)},
        sheets=[
            Sheet(
                id="top",
                symbols=[
                    Symbol(
                        id="j2a",
                        refdes="J2A",
                        pins=[Pin(name="4", location=(0, 0))],
                    ),
                    Symbol(
                        id="u16a",
                        refdes="U16A",
                        pins=[Pin(name="40", location=(20, 0))],
                    ),
                ],
                wires=[WireSegment(points=[(0, 0), (20, 0)])],
                nets=[Net(name="OAA1", anchor=(10, 0), is_global=False)],
            )
        ],
    )

    assert convert_schematic_to_netlist(schematic).normalized() == (
        (
            "OAAA",
            (
                ("schematic_pin", "j2a", "J2A", "4"),
                ("schematic_pin", "u16a", "U16A", "40"),
            ),
        ),
    )


def test_altium_schematic_uses_question_mark_placeholders_for_nc_single_pin_nets() -> (
    None
):
    schematic = Schematic(
        top_sheet_id="top",
        extra_properties={"netlist_naming_policy": dict(_QUESTION_MARK_POLICY)},
        sheets=[
            Sheet(
                id="top",
                symbols=[
                    Symbol(
                        id="u14",
                        refdes="U14",
                        pins=[
                            Pin(
                                name="23",
                                location=(0, 0),
                                extra_properties={"pin_name": "NC"},
                            )
                        ],
                    )
                ],
            )
        ],
    )

    assert convert_schematic_to_netlist(schematic).normalized() == (
        (
            "?1",
            (("schematic_pin", "u14", "U14", "23"),),
        ),
    )


def test_pcb_hl_projects_into_the_same_netlist_boundary() -> None:
    pcb = PCB(
        collections=[
            _component(
                component_id="u1",
                refdes="U1",
                pads=[
                    _pad(
                        component_id="u1",
                        pad_name="1",
                        x=0,
                        y=0,
                        layers=["F.Cu"],
                        net_name="SIG",
                    ),
                    _pad(
                        component_id="u1",
                        pad_name="2",
                        x=0,
                        y=20,
                        layers=["F.Cu"],
                        net_name="GND",
                    ),
                ],
            ),
            _component(
                component_id="r1",
                refdes="R1",
                pads=[
                    _pad(
                        component_id="r1",
                        pad_name="1",
                        x=20,
                        y=0,
                        layers=["B.Cu"],
                    ),
                    _pad(
                        component_id="r1",
                        pad_name="2",
                        x=20,
                        y=20,
                        layers=["F.Cu"],
                    ),
                ],
            ),
        ],
        geometries=[
            _track(start=(0, 0), end=(10, 0), layer="F.Cu"),
            _track(start=(10, 0), end=(20, 0), layer="B.Cu"),
            _via(x=10, y=0, layers=["F.Cu", "B.Cu"]),
            _region(
                vertices=[(-5, 15), (25, 15), (25, 25), (-5, 25)],
                layer="F.Cu",
                net_name="GND",
            ),
        ],
    )

    assert convert_pcb_to_netlist(pcb).normalized() == (
        (
            "GND",
            (
                ("pcb_pad", "r1", "R1", "2"),
                ("pcb_pad", "u1", "U1", "2"),
            ),
        ),
        (
            "SIG",
            (
                ("pcb_pad", "r1", "R1", "1"),
                ("pcb_pad", "u1", "U1", "1"),
            ),
        ),
    )


def test_rectangular_pad_geometry_does_not_false_short_nearby_track_endpoints() -> None:
    pcb = PCB(
        collections=[
            _component(
                component_id="u1",
                refdes="U1",
                pads=[
                    _rect_pad(
                        component_id="u1",
                        pad_name="1",
                        x=0,
                        y=0,
                        width=4.0,
                        height=1.0,
                        layers=["F.Cu"],
                    )
                ],
            ),
            _component(
                component_id="j1",
                refdes="J1",
                pads=[
                    _obround_pad(
                        component_id="j1",
                        pad_name="1",
                        x=3.0,
                        y=0.9,
                        width=1.8,
                        height=0.8,
                        layers=["F.Cu"],
                        net_name="SIG",
                    )
                ],
            ),
        ],
        geometries=[
            _track(
                start=(3.0, 0.9),
                end=(1.9, 0.9),
                layer="F.Cu",
                net_name="SIG",
            )
        ],
    )

    assert convert_pcb_to_netlist(pcb, include_unconnected=True).normalized() == (
        (
            "SIG",
            (("pcb_pad", "j1", "J1", "1"),),
        ),
        (
            "net-1",
            (("pcb_pad", "u1", "U1", "1"),),
        ),
    )


def test_hierarchical_schematic_connectivity_flows_through_sheet_symbols() -> None:
    schematic = Schematic(
        top_sheet_id="top",
        sheets=[
            Sheet(
                id="top",
                symbols=[
                    Symbol(
                        id="j1",
                        refdes="J1",
                        pins=[Pin(name="1", location=(0, 0))],
                    ),
                    Symbol(
                        kind="sheet",
                        child_sheet_id="child",
                        pins=[Pin(name="SIG_IN", location=(10, 0))],
                    ),
                ],
                wires=[WireSegment(points=[(0, 0), (10, 0)])],
            ),
            Sheet(
                id="child",
                pins=[Pin(name="SIG_IN", location=(0, 0))],
                symbols=[
                    Symbol(
                        id="u1",
                        refdes="U1",
                        pins=[Pin(name="A", location=(20, 0))],
                    )
                ],
                wires=[WireSegment(points=[(0, 0), (20, 0)])],
                nets=[Net(name="SIG", anchor=(5, 0))],
            ),
        ],
    )

    assert convert_schematic_to_netlist(schematic).normalized() == (
        (
            "SIG",
            (
                ("schematic_pin", "j1", "J1", "1"),
                ("schematic_pin", "u1", "U1", "A"),
            ),
        ),
    )


def test_local_and_global_labels_with_same_raw_name_merge() -> None:
    schematic = Schematic(
        top_sheet_id="top",
        sheets=[
            Sheet(
                id="top",
                name="Ethernet",
                symbols=[
                    Symbol(
                        id="r155",
                        refdes="R155",
                        pins=[Pin(name="1", location=(0, 0))],
                    ),
                    Symbol(
                        id="u20",
                        refdes="U20",
                        pins=[Pin(name="37", location=(20, 0))],
                    ),
                ],
                wires=[
                    WireSegment(points=[(0, 0), (5, 0)]),
                    WireSegment(points=[(20, 0), (25, 0)]),
                ],
                nets=[
                    Net(
                        name="/Ethernet/ETH_MDIO",
                        anchor=(5, 0),
                        is_global=False,
                        extra_properties={
                            "kind": "label",
                            "sheet_path": "/Ethernet/",
                            "sheet_depth": 1,
                            "raw_name": "ETH_MDIO",
                        },
                    ),
                    Net(
                        name="ETH_MDIO",
                        anchor=(25, 0),
                        is_global=True,
                        extra_properties={
                            "kind": "global_label",
                            "sheet_path": "/Ethernet/",
                            "sheet_depth": -1,
                            "raw_name": "ETH_MDIO",
                            "global": True,
                        },
                    ),
                ],
            )
        ],
    )

    assert convert_schematic_to_netlist(schematic).normalized() == (
        (
            "ETH_MDIO",
            (
                ("schematic_pin", "r155", "R155", "1"),
                ("schematic_pin", "u20", "U20", "37"),
            ),
        ),
    )


def test_schematic_and_pcb_hl_can_be_compared_via_the_shared_netlist_shape() -> None:
    schematic = Schematic(
        top_sheet_id="top",
        sheets=[
            Sheet(
                id="top",
                symbols=[
                    Symbol(
                        id="u1",
                        refdes="U1",
                        pins=[
                            Pin(name="A", location=(0, 0)),
                            Pin(name="B", location=(0, 20)),
                        ],
                    ),
                    Symbol(
                        id="j1",
                        refdes="J1",
                        pins=[
                            Pin(name="1", location=(20, 0)),
                            Pin(name="2", location=(20, 20)),
                        ],
                    ),
                ],
                wires=[
                    WireSegment(points=[(0, 0), (20, 0)]),
                    WireSegment(points=[(0, 20), (20, 20)]),
                ],
                nets=[
                    Net(name="SIG", anchor=(10, 0)),
                    Net(name="GND", anchor=(10, 20), is_power=True),
                ],
            )
        ],
    )
    pcb = PCB(
        collections=[
            _component(
                component_id="u1",
                refdes="U1",
                pads=[
                    _pad(
                        component_id="u1",
                        pad_name="A",
                        x=0,
                        y=0,
                        layers=["F.Cu"],
                        net_name="SIG",
                    ),
                    _pad(
                        component_id="u1",
                        pad_name="B",
                        x=0,
                        y=20,
                        layers=["F.Cu"],
                        net_name="GND",
                    ),
                ],
            ),
            _component(
                component_id="j1",
                refdes="J1",
                pads=[
                    _pad(
                        component_id="j1",
                        pad_name="1",
                        x=20,
                        y=0,
                        layers=["F.Cu"],
                        net_name="SIG",
                    ),
                    _pad(
                        component_id="j1",
                        pad_name="2",
                        x=20,
                        y=20,
                        layers=["F.Cu"],
                        net_name="GND",
                    ),
                ],
            ),
        ]
    )

    schematic_projection = {
        net_name: {(owner, terminal) for _, owner, _, terminal in terminals}
        for net_name, terminals in convert_schematic_to_netlist(schematic).normalized()
    }
    pcb_projection = {
        net_name: {(owner, terminal) for _, owner, _, terminal in terminals}
        for net_name, terminals in convert_pcb_to_netlist(pcb).normalized()
    }

    assert schematic_projection == pcb_projection
