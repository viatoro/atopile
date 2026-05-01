"""JSON ↔ LL parse/dump for the DeepPCB board and constraints formats."""

from __future__ import annotations

import json
from pathlib import Path

from faebryk.libs.eda.deeppcb.models import ll

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load(path_or_str: Path | str) -> ll.DeepPCBBoard:
    """Parse a ``.deeppcb`` JSON file or string into LL models."""
    text = (
        path_or_str.read_text(encoding="utf-8")
        if isinstance(path_or_str, Path)
        else path_or_str
    )
    return _deserialize_board(json.loads(text))


def dump(board: ll.DeepPCBBoard) -> str:
    """Serialize an LL board model back to JSON."""
    return json.dumps(_serialize_board(board), indent=2)


def load_constraints(path_or_str: Path | str) -> ll.DeepPCBConstraints:
    """Parse a DeepPCB constraints JSON file or string into LL models."""
    text = (
        path_or_str.read_text(encoding="utf-8")
        if isinstance(path_or_str, Path)
        else path_or_str
    )
    return _deserialize_constraints(json.loads(text))


def dump_constraints(constraints: ll.DeepPCBConstraints) -> str:
    """Serialize an LL constraints model back to JSON."""
    return json.dumps(_serialize_constraints(constraints), indent=2)


# ---------------------------------------------------------------------------
# Shape serialization (shared by many types)
# ---------------------------------------------------------------------------


def _deserialize_shape(d: dict) -> ll.Shape:
    t = ll.ShapeType(d["type"])
    shape = ll.Shape(type=t)
    if t == ll.ShapeType.CIRCLE:
        shape.center = d["center"]
        shape.radius = d["radius"]
    elif t == ll.ShapeType.RECTANGLE:
        shape.lower_left = d["lowerLeft"]
        shape.upper_right = d["upperRight"]
    elif t in (ll.ShapeType.POLYLINE, ll.ShapeType.POLYGON):
        shape.points = d["points"]
    elif t == ll.ShapeType.PATH:
        shape.points = d["points"]
        shape.width = d["width"]
    elif t == ll.ShapeType.POLYGON_WITH_HOLES:
        shape.outline = d["outline"]
        shape.holes = d.get("holes", [])
    elif t == ll.ShapeType.MULTI:
        shape.shapes = [_deserialize_shape(s) for s in d["shapes"]]
    else:
        raise ValueError(f"Unknown shape type: {t!r}")
    if "cornerRadius" in d:
        shape.corner_radius = d["cornerRadius"]
    return shape


def _serialize_shape(shape: ll.Shape) -> dict:
    d: dict = {"type": shape.type}
    if shape.type == ll.ShapeType.CIRCLE:
        d["center"] = shape.center
        d["radius"] = shape.radius
    elif shape.type == ll.ShapeType.RECTANGLE:
        d["lowerLeft"] = shape.lower_left
        d["upperRight"] = shape.upper_right
    elif shape.type in (ll.ShapeType.POLYLINE, ll.ShapeType.POLYGON):
        d["points"] = shape.points
    elif shape.type == ll.ShapeType.PATH:
        d["points"] = shape.points
        d["width"] = shape.width
    elif shape.type == ll.ShapeType.POLYGON_WITH_HOLES:
        d["outline"] = shape.outline
        d["holes"] = shape.holes
    elif shape.type == ll.ShapeType.MULTI:
        d["shapes"] = [_serialize_shape(s) for s in shape.shapes]  # type: ignore[union-attr]
    if shape.corner_radius is not None:
        d["cornerRadius"] = shape.corner_radius
    return d


# ---------------------------------------------------------------------------
# Board deserialization
# ---------------------------------------------------------------------------


def _deserialize_resolution(d: dict) -> ll.Resolution:
    return ll.Resolution(unit=ll.ResolutionUnit(d["unit"]), value=d["value"])


def _deserialize_boundary(d: dict) -> ll.Boundary:
    return ll.Boundary(
        shape=_deserialize_shape(d["shape"]),
        clearance=d["clearance"],
        user_data=d.get("userData"),
    )


def _deserialize_padstack_pad(d: dict) -> ll.PadstackPad:
    return ll.PadstackPad(
        shape=_deserialize_shape(d["shape"]),
        layer_from=d["layerFrom"],
        layer_to=d["layerTo"],
    )


def _deserialize_padstack(d: dict) -> ll.Padstack:
    return ll.Padstack(
        id=d["id"],
        shape=_deserialize_shape(d["shape"]) if "shape" in d else None,
        layers=d.get("layers"),
        allow_via=d.get("allowVia", False),
        pads=[_deserialize_padstack_pad(p) for p in d["pads"]] if "pads" in d else None,
        hole=ll.PadstackHole(shape=_deserialize_shape(d["hole"]["shape"]))
        if "hole" in d
        else None,
    )


def _deserialize_pin(d: dict) -> ll.Pin:
    return ll.Pin(
        id=d["id"],
        padstack=d["padstack"],
        position=d["position"],
        rotation=d["rotation"],
    )


def _deserialize_keepout(d: dict) -> ll.Keepout:
    raw_type = d.get("type")
    if raw_type is not None:
        if isinstance(raw_type, list):
            keepout_type: list[ll.KeepoutItemType] | ll.KeepoutItemType | None = [
                ll.KeepoutItemType(t.lower()) for t in raw_type
            ]
        else:
            keepout_type = ll.KeepoutItemType(raw_type.lower())
    else:
        keepout_type = None
    return ll.Keepout(
        shape=_deserialize_shape(d["shape"]),
        layer=d["layer"],
        type=keepout_type,
        user_data=d.get("userData"),
    )


def _deserialize_component_definition(d: dict) -> ll.ComponentDefinition:
    return ll.ComponentDefinition(
        id=d["id"],
        pins=[_deserialize_pin(p) for p in d["pins"]],
        keepouts=[_deserialize_keepout(k) for k in d["keepouts"]],
        outline=_deserialize_shape(d["outline"]) if d.get("outline") else None,
    )


def _deserialize_component(d: dict) -> ll.Component:
    return ll.Component(
        id=d["id"],
        definition=d["definition"],
        position=d["position"],
        rotation=d["rotation"],
        side=ll.ComponentSide(d["side"]),
        part_number=d.get("partNumber"),
        protected=d.get("protected", False),
        user_data=d.get("userData"),
    )


def _deserialize_layer(d: dict) -> ll.Layer:
    return ll.Layer(
        id=d["id"],
        keepouts=[_deserialize_keepout(k) for k in d["keepouts"]],
        display_name=d.get("displayName"),
        type=ll.LayerType(d["type"]) if "type" in d else None,
    )


def _deserialize_net(d: dict) -> ll.Net:
    return ll.Net(
        id=d["id"],
        pins=d["pins"],
        track_width=d.get("trackWidth"),
        routing_priority=d.get("routingPriority"),
        forbidden_layers=d.get("forbiddenLayers"),
    )


def _deserialize_net_class(d: dict) -> ll.NetClass:
    return ll.NetClass(
        id=d["id"],
        nets=d["nets"],
        clearance=d["clearance"],
        track_width=d["trackWidth"],
        via_definition=d.get("viaDefinition"),
        via_priority=d.get("viaPriority"),
    )


def _deserialize_wire(d: dict) -> ll.Wire:
    return ll.Wire(
        net_id=d["netId"],
        layer=d["layer"],
        start=d["start"],
        end=d["end"],
        width=d["width"],
        type=ll.WireType(d.get("type", "segment")),
        protected=d.get("protected", False),
        user_data=d.get("userData"),
    )


def _deserialize_via(d: dict) -> ll.Via:
    return ll.Via(
        net_id=d["netId"],
        position=d["position"],
        padstack=d["padstack"],
        protected=d.get("protected", False),
        user_data=d.get("userData"),
    )


def _deserialize_plane(d: dict) -> ll.Plane:
    raw_kr = d.get("keepoutRule")
    if raw_kr is not None:
        if isinstance(raw_kr, list):
            keepout_rule: list[ll.PlaneKeepoutRule] | ll.PlaneKeepoutRule | None = [
                ll.PlaneKeepoutRule(k) for k in raw_kr
            ]
        else:
            keepout_rule = ll.PlaneKeepoutRule(raw_kr)
    else:
        keepout_rule = None
    return ll.Plane(
        net_id=d["netId"],
        layer=d["layer"],
        shape=_deserialize_shape(d["shape"]),
        protected=d.get("protected", False),
        keepout_rule=keepout_rule,
        user_data=d.get("userData"),
        filled_shape=[_deserialize_shape(s) for s in d["filledShape"]]
        if "filledShape" in d
        else None,
    )


def _deserialize_net_preference(d: dict) -> ll.NetPreference:
    return ll.NetPreference(
        id=d["id"],
        nets=d["nets"],
        reduce_via_count_prio_coef=d.get("reduceViaCountPrioCoef", 1),
        reduce_wire_length_prio_coef=d.get("reduceWireLengthPrioCoef", 1),
        reduce_acute_angle_prio_coef=d.get("reduceAcuteAnglePrioCoef", 1),
    )


def _deserialize_differential_pair(d: dict) -> ll.DifferentialPair:
    return ll.DifferentialPair(
        net_id1=d["netId1"],
        net_id2=d["netId2"],
        track_width=d.get("trackWidth"),
        gap=d.get("gap"),
    )


def _deserialize_rule_subject(d: dict) -> ll.RuleSubject:
    return ll.RuleSubject(id=d["id"], type=ll.RuleSubjectType(d["type"]))


def _deserialize_rule(d: dict) -> ll.Rule:
    rule_type = ll.RuleType(d["type"])
    value = d["value"]
    if rule_type == ll.RuleType.ROUTING_DIRECTION and isinstance(value, str):
        value = ll.RoutingDirection(value)
    elif rule_type == ll.RuleType.PIN_CONNECTION_POINT and isinstance(value, str):
        value = ll.PinConnectionPointValue(value)
    raw_desc = d.get("description")
    try:
        description: ll.RuleDescription | str | None = (
            ll.RuleDescription(raw_desc) if raw_desc else None
        )
    except ValueError:
        description = raw_desc
    return ll.Rule(
        type=rule_type,
        value=value,
        subjects=[_deserialize_rule_subject(s) for s in d.get("subjects", [])],
        description=description,
    )


def _deserialize_board(data: dict) -> ll.DeepPCBBoard:
    return ll.DeepPCBBoard(
        name=data["name"],
        resolution=_deserialize_resolution(data["resolution"]),
        boundary=_deserialize_boundary(data["boundary"]),
        padstacks=[_deserialize_padstack(p) for p in data["padstacks"]],
        component_definitions=[
            _deserialize_component_definition(cd) for cd in data["componentDefinitions"]
        ],
        components=[_deserialize_component(c) for c in data["components"]],
        layers=[_deserialize_layer(layer) for layer in data["layers"]],
        nets=[_deserialize_net(n) for n in data["nets"]],
        net_classes=[_deserialize_net_class(nc) for nc in data["netClasses"]],
        planes=[_deserialize_plane(p) for p in data["planes"]],
        wires=[_deserialize_wire(w) for w in data["wires"]],
        vias=[_deserialize_via(v) for v in data["vias"]],
        via_definitions=data["viaDefinitions"],
        net_preferences=[
            _deserialize_net_preference(np) for np in data.get("netPreferences", [])
        ],
        differential_pairs=[
            _deserialize_differential_pair(dp)
            for dp in data.get("differentialPairs", [])
        ],
        rules=[_deserialize_rule(r) for r in data.get("rules", [])],
    )


# ---------------------------------------------------------------------------
# Board serialization
# ---------------------------------------------------------------------------


def _serialize_resolution(res: ll.Resolution) -> dict:
    return {"unit": res.unit, "value": res.value}


def _serialize_boundary(b: ll.Boundary) -> dict:
    d: dict = {"shape": _serialize_shape(b.shape), "clearance": b.clearance}
    if b.user_data is not None:
        d["userData"] = b.user_data
    return d


def _serialize_padstack_pad(p: ll.PadstackPad) -> dict:
    return {
        "shape": _serialize_shape(p.shape),
        "layerFrom": p.layer_from,
        "layerTo": p.layer_to,
    }


def _serialize_padstack(ps: ll.Padstack) -> dict:
    d: dict = {"id": ps.id}
    if ps.shape is not None:
        d["shape"] = _serialize_shape(ps.shape)
    if ps.layers is not None:
        d["layers"] = ps.layers
    if ps.allow_via:
        d["allowVia"] = ps.allow_via
    else:
        d["allowVia"] = False
    if ps.pads is not None:
        d["pads"] = [_serialize_padstack_pad(p) for p in ps.pads]
    if ps.hole is not None:
        d["hole"] = {"shape": _serialize_shape(ps.hole.shape)}
    return d


def _serialize_pin(pin: ll.Pin) -> dict:
    return {
        "id": pin.id,
        "padstack": pin.padstack,
        "position": pin.position,
        "rotation": pin.rotation,
    }


def _serialize_keepout(k: ll.Keepout) -> dict:
    d: dict = {"shape": _serialize_shape(k.shape), "layer": k.layer}
    if k.type is not None:
        d["type"] = k.type
    if k.user_data is not None:
        d["userData"] = k.user_data
    return d


def _serialize_component_definition(cd: ll.ComponentDefinition) -> dict:
    d: dict = {
        "id": cd.id,
        "pins": [_serialize_pin(p) for p in cd.pins],
        "keepouts": [_serialize_keepout(k) for k in cd.keepouts],
    }
    if cd.outline is not None:
        d["outline"] = _serialize_shape(cd.outline)
    return d


def _serialize_component(c: ll.Component) -> dict:
    d: dict = {
        "id": c.id,
        "definition": c.definition,
        "position": c.position,
        "rotation": c.rotation,
        "side": c.side,
    }
    if c.part_number is not None:
        d["partNumber"] = c.part_number
    if c.protected:
        d["protected"] = c.protected
    if c.user_data is not None:
        d["userData"] = c.user_data
    return d


def _serialize_layer(layer: ll.Layer) -> dict:
    d: dict = {"id": layer.id}
    if layer.display_name is not None:
        d["displayName"] = layer.display_name
    d["keepouts"] = [_serialize_keepout(k) for k in layer.keepouts]
    if layer.type is not None:
        d["type"] = layer.type
    return d


def _serialize_net(net: ll.Net) -> dict:
    d: dict = {"id": net.id, "pins": net.pins}
    if net.track_width is not None:
        d["trackWidth"] = net.track_width
    if net.routing_priority is not None:
        d["routingPriority"] = net.routing_priority
    if net.forbidden_layers is not None:
        d["forbiddenLayers"] = net.forbidden_layers
    return d


def _serialize_net_class(nc: ll.NetClass) -> dict:
    d: dict = {
        "id": nc.id,
        "nets": nc.nets,
        "clearance": nc.clearance,
        "trackWidth": nc.track_width,
    }
    if nc.via_definition is not None:
        d["viaDefinition"] = nc.via_definition
    if nc.via_priority is not None:
        d["viaPriority"] = nc.via_priority
    return d


def _serialize_wire(w: ll.Wire) -> dict:
    d: dict = {
        "netId": w.net_id,
        "layer": w.layer,
        "start": w.start,
        "end": w.end,
        "width": w.width,
        "type": w.type,
    }
    if w.protected:
        d["protected"] = w.protected
    if w.user_data is not None:
        d["userData"] = w.user_data
    return d


def _serialize_via(v: ll.Via) -> dict:
    d: dict = {
        "netId": v.net_id,
        "position": v.position,
        "padstack": v.padstack,
    }
    if v.protected:
        d["protected"] = v.protected
    if v.user_data is not None:
        d["userData"] = v.user_data
    return d


def _serialize_plane(p: ll.Plane) -> dict:
    d: dict = {
        "netId": p.net_id,
        "layer": p.layer,
        "shape": _serialize_shape(p.shape),
    }
    if p.protected:
        d["protected"] = p.protected
    if p.keepout_rule is not None:
        d["keepoutRule"] = p.keepout_rule
    if p.user_data is not None:
        d["userData"] = p.user_data
    if p.filled_shape is not None:
        d["filledShape"] = [_serialize_shape(s) for s in p.filled_shape]
    return d


def _serialize_net_preference(np: ll.NetPreference) -> dict:
    return {
        "id": np.id,
        "nets": np.nets,
        "reduceViaCountPrioCoef": np.reduce_via_count_prio_coef,
        "reduceWireLengthPrioCoef": np.reduce_wire_length_prio_coef,
        "reduceAcuteAnglePrioCoef": np.reduce_acute_angle_prio_coef,
    }


def _serialize_differential_pair(dp: ll.DifferentialPair) -> dict:
    d: dict = {"netId1": dp.net_id1, "netId2": dp.net_id2}
    if dp.track_width is not None:
        d["trackWidth"] = dp.track_width
    if dp.gap is not None:
        d["gap"] = dp.gap
    return d


def _serialize_rule_subject(s: ll.RuleSubject) -> dict:
    return {"id": s.id, "type": s.type}


def _serialize_rule(r: ll.Rule) -> dict:
    d: dict = {
        "type": r.type,
        "value": r.value,
        "subjects": [_serialize_rule_subject(s) for s in r.subjects],
    }
    if r.description is not None:
        d["description"] = r.description
    return d


def _serialize_board(board: ll.DeepPCBBoard) -> dict:
    d: dict = {
        "name": board.name,
        "resolution": _serialize_resolution(board.resolution),
        "boundary": _serialize_boundary(board.boundary),
        "padstacks": [_serialize_padstack(p) for p in board.padstacks],
        "componentDefinitions": [
            _serialize_component_definition(cd) for cd in board.component_definitions
        ],
        "components": [_serialize_component(c) for c in board.components],
        "layers": [_serialize_layer(layer) for layer in board.layers],
        "nets": [_serialize_net(n) for n in board.nets],
        "netClasses": [_serialize_net_class(nc) for nc in board.net_classes],
        "planes": [_serialize_plane(p) for p in board.planes],
        "wires": [_serialize_wire(w) for w in board.wires],
        "vias": [_serialize_via(v) for v in board.vias],
        "viaDefinitions": board.via_definitions,
    }
    # Always emit these fields — the DeepPCB API requires them even when empty.
    d["netPreferences"] = [
        _serialize_net_preference(np) for np in board.net_preferences
    ]
    d["differentialPairs"] = [
        _serialize_differential_pair(dp) for dp in board.differential_pairs
    ]
    d["rules"] = [_serialize_rule(r) for r in board.rules]
    return d


# ---------------------------------------------------------------------------
# Constraints deserialization / serialization
# ---------------------------------------------------------------------------


def _deserialize_decoupling_target(d: dict) -> ll.DecouplingTarget:
    return ll.DecouplingTarget(type=ll.ConstraintType(d["type"]), targets=d["targets"])


def _deserialize_constraints(data: dict) -> ll.DeepPCBConstraints:
    return ll.DeepPCBConstraints(
        decoupling_constraints={
            pin_id: [_deserialize_decoupling_target(t) for t in targets]
            for pin_id, targets in data.get("decoupling_constraints", {}).items()
        },
        net_type_constraints=[
            ll.NetTypeConstraint(
                type=ll.ConstraintType(c["type"]), targets=c["targets"]
            )
            for c in data.get("net_type_constraints", [])
        ],
    )


def _serialize_constraints(c: ll.DeepPCBConstraints) -> dict:
    return {
        "decoupling_constraints": {
            pin_id: [{"type": t.type, "targets": t.targets} for t in targets]
            for pin_id, targets in c.decoupling_constraints.items()
        },
        "net_type_constraints": [
            {"type": ntc.type, "targets": ntc.targets} for ntc in c.net_type_constraints
        ],
    }
