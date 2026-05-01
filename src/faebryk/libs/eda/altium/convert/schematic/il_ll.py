"""Bidirectional translation between Altium schematic LL and IL."""

from __future__ import annotations

from dataclasses import dataclass

from faebryk.libs.eda.altium.models.schematic.il import (
    AltiumSchematic,
    SchematicBus,
    SchematicComponent,
    SchematicJunction,
    SchematicNetLabel,
    SchematicParameter,
    SchematicPin,
    SchematicPort,
    SchematicPowerObject,
    SchematicSheetEntry,
    SchematicSheetSymbol,
    SchematicWire,
)
from faebryk.libs.eda.altium.models.schematic.ll import (
    AltiumSchDoc,
    BusRecord,
    ComponentRecord,
    JunctionRecord,
    NetLabelRecord,
    ParameterRecord,
    PinRecord,
    PortRecord,
    PowerObjectRecord,
    SchematicRecord,
    SchRecordType,
    SheetEntryRecord,
    SheetSymbolRecord,
    UnknownRecord,
    WireRecord,
)

_WIRE_VERTEX_SCALE = 100
_SHEET_NAME_RECORD = 32
_SHEET_FILE_NAME_RECORD = 33


@dataclass
class Context:
    component_by_index: dict[int, SchematicComponent]
    sheet_symbol_by_index: dict[int, SchematicSheetSymbol]


def _ll_properties(item) -> dict[str, str]:
    properties = item.extra_properties.get("ll_properties")
    if isinstance(properties, dict):
        return dict(properties)
    return {}


def _wire_vertex_to_il(point: tuple[int, int]) -> tuple[int, int]:
    return (point[0] * _WIRE_VERTEX_SCALE, point[1] * _WIRE_VERTEX_SCALE)


def _wire_vertex_to_ll(point: tuple[int, int]) -> tuple[int, int]:
    return (
        int(round(point[0] / _WIRE_VERTEX_SCALE)),
        int(round(point[1] / _WIRE_VERTEX_SCALE)),
    )


class ComponentCodec:
    @staticmethod
    def decode(record: ComponentRecord) -> SchematicComponent:
        return SchematicComponent(
            id=f"component-{record.index + 1}",
            source_index=record.index,
            source_order=record.index,
            extra_properties={"ll_properties": dict(record.properties)},
            lib_reference=record.lib_reference,
            design_item_id=record.design_item_id,
            description=record.description,
            location=record.location,
            orientation=record.orientation,
            current_part_id=record.current_part_id,
            part_count=record.part_count,
            display_mode_count=record.display_mode_count,
        )

    @staticmethod
    def encode(component: SchematicComponent, index: int) -> ComponentRecord:
        return ComponentRecord(
            index=index,
            owner_index=-1,
            owner_part_id=-1,
            owner_part_display_mode=0,
            unique_id=None,
            properties=_ll_properties(component),
            lib_reference=component.lib_reference,
            design_item_id=component.design_item_id,
            description=component.description,
            location=component.location,
            orientation=component.orientation,
            current_part_id=component.current_part_id,
            part_count=component.part_count,
            display_mode_count=component.display_mode_count,
        )


class PinCodec:
    @staticmethod
    def decode(record: PinRecord) -> SchematicPin:
        return SchematicPin(
            id=f"pin-{record.index + 1}",
            source_index=record.index,
            source_order=record.index,
            extra_properties={"ll_properties": dict(record.properties)},
            name=record.name,
            designator=record.designator,
            location=record.location,
            length=record.length,
            electrical=record.electrical,
            orientation=record.orientation,
            show_name=record.show_name,
            show_designator=record.show_designator,
            description=record.description,
            owner_part_id=record.owner_part_id,
            owner_part_display_mode=record.owner_part_display_mode,
        )

    @staticmethod
    def encode(pin: SchematicPin, index: int, owner_index: int) -> PinRecord:
        return PinRecord(
            index=index,
            owner_index=owner_index,
            owner_part_id=pin.owner_part_id,
            owner_part_display_mode=pin.owner_part_display_mode,
            unique_id=None,
            properties=_ll_properties(pin),
            name=pin.name,
            designator=pin.designator,
            location=pin.location,
            length=pin.length,
            electrical=pin.electrical,
            orientation=pin.orientation,
            show_name=pin.show_name,
            show_designator=pin.show_designator,
            description=pin.description,
        )


class ParameterCodec:
    @staticmethod
    def decode(record: ParameterRecord) -> SchematicParameter:
        return SchematicParameter(
            id=f"parameter-{record.index + 1}",
            source_index=record.index,
            source_order=record.index,
            extra_properties={
                "ll_properties": dict(record.properties),
                "record_type": record.record_type,
            },
            name=record.name,
            text=record.text,
            location=record.location,
            color=record.color,
            font_id=record.font_id,
            is_hidden=record.is_hidden,
            is_designator=record.is_designator,
            read_only_state=record.read_only_state,
            owner_part_id=record.owner_part_id,
            owner_part_display_mode=record.owner_part_display_mode,
        )

    @staticmethod
    def encode(
        parameter: SchematicParameter,
        index: int,
        owner_index: int = -1,
    ) -> ParameterRecord:
        record_type = parameter.extra_properties.get("record_type")
        if not isinstance(record_type, int):
            record_type = (
                int(SchRecordType.DESIGNATOR)
                if parameter.is_designator
                else int(SchRecordType.PARAMETER)
            )
        return ParameterRecord(
            index=index,
            record_type=record_type,
            owner_index=owner_index,
            owner_part_id=parameter.owner_part_id,
            owner_part_display_mode=parameter.owner_part_display_mode,
            unique_id=None,
            properties=_ll_properties(parameter),
            name=parameter.name,
            text=parameter.text,
            location=parameter.location,
            color=parameter.color,
            font_id=parameter.font_id,
            is_hidden=parameter.is_hidden,
            is_designator=parameter.is_designator,
            read_only_state=parameter.read_only_state,
        )


class WireCodec:
    @staticmethod
    def decode(record: WireRecord) -> SchematicWire:
        return SchematicWire(
            id=f"wire-{record.index + 1}",
            source_index=record.index,
            source_order=record.index,
            extra_properties={"ll_properties": dict(record.properties)},
            vertices=[_wire_vertex_to_il(vertex) for vertex in record.vertices],
            color=record.color,
            line_width=record.line_width,
            owner_part_id=record.owner_part_id,
            owner_part_display_mode=record.owner_part_display_mode,
        )

    @staticmethod
    def encode(wire: SchematicWire, index: int, owner_index: int = -1) -> WireRecord:
        return WireRecord(
            index=index,
            owner_index=owner_index,
            owner_part_id=wire.owner_part_id,
            owner_part_display_mode=wire.owner_part_display_mode,
            unique_id=None,
            properties=_ll_properties(wire),
            vertices=[_wire_vertex_to_ll(vertex) for vertex in wire.vertices],
            color=wire.color,
            line_width=wire.line_width,
        )


class BusCodec:
    @staticmethod
    def decode(record: BusRecord) -> SchematicBus:
        return SchematicBus(
            id=f"bus-{record.index + 1}",
            source_index=record.index,
            source_order=record.index,
            extra_properties={"ll_properties": dict(record.properties)},
            vertices=[_wire_vertex_to_il(vertex) for vertex in record.vertices],
            color=record.color,
            line_width=record.line_width,
            owner_part_id=record.owner_part_id,
            owner_part_display_mode=record.owner_part_display_mode,
        )

    @staticmethod
    def encode(bus: SchematicBus, index: int, owner_index: int = -1) -> BusRecord:
        return BusRecord(
            index=index,
            owner_index=owner_index,
            owner_part_id=bus.owner_part_id,
            owner_part_display_mode=bus.owner_part_display_mode,
            unique_id=None,
            properties=_ll_properties(bus),
            vertices=[_wire_vertex_to_ll(vertex) for vertex in bus.vertices],
            color=bus.color,
            line_width=bus.line_width,
        )


class NetLabelCodec:
    @staticmethod
    def decode(record: NetLabelRecord) -> SchematicNetLabel:
        return SchematicNetLabel(
            id=f"net-label-{record.index + 1}",
            source_index=record.index,
            source_order=record.index,
            extra_properties={"ll_properties": dict(record.properties)},
            text=record.text,
            location=record.location,
            color=record.color,
            font_id=record.font_id,
            owner_part_id=record.owner_part_id,
            owner_part_display_mode=record.owner_part_display_mode,
        )

    @staticmethod
    def encode(
        net_label: SchematicNetLabel,
        index: int,
        owner_index: int = -1,
    ) -> NetLabelRecord:
        return NetLabelRecord(
            index=index,
            owner_index=owner_index,
            owner_part_id=net_label.owner_part_id,
            owner_part_display_mode=net_label.owner_part_display_mode,
            unique_id=None,
            properties=_ll_properties(net_label),
            text=net_label.text,
            location=net_label.location,
            color=net_label.color,
            font_id=net_label.font_id,
        )


class JunctionCodec:
    @staticmethod
    def decode(record: JunctionRecord) -> SchematicJunction:
        return SchematicJunction(
            id=f"junction-{record.index + 1}",
            source_index=record.index,
            source_order=record.index,
            extra_properties={"ll_properties": dict(record.properties)},
            location=record.location,
            color=record.color,
            owner_part_id=record.owner_part_id,
            owner_part_display_mode=record.owner_part_display_mode,
        )

    @staticmethod
    def encode(
        junction: SchematicJunction,
        index: int,
        owner_index: int = -1,
    ) -> JunctionRecord:
        return JunctionRecord(
            index=index,
            owner_index=owner_index,
            owner_part_id=junction.owner_part_id,
            owner_part_display_mode=junction.owner_part_display_mode,
            unique_id=None,
            properties=_ll_properties(junction),
            location=junction.location,
            color=junction.color,
        )


class PowerObjectCodec:
    @staticmethod
    def decode(record: PowerObjectRecord) -> SchematicPowerObject:
        return SchematicPowerObject(
            id=f"power-{record.index + 1}",
            source_index=record.index,
            source_order=record.index,
            extra_properties={"ll_properties": dict(record.properties)},
            text=record.text,
            location=record.location,
            orientation=record.orientation,
            style=record.style,
            color=record.color,
            font_id=record.font_id,
            show_net_name=record.show_net_name,
            owner_part_id=record.owner_part_id,
            owner_part_display_mode=record.owner_part_display_mode,
        )

    @staticmethod
    def encode(
        power_object: SchematicPowerObject,
        index: int,
        owner_index: int = -1,
    ) -> PowerObjectRecord:
        return PowerObjectRecord(
            index=index,
            owner_index=owner_index,
            owner_part_id=power_object.owner_part_id,
            owner_part_display_mode=power_object.owner_part_display_mode,
            unique_id=None,
            properties=_ll_properties(power_object),
            text=power_object.text,
            location=power_object.location,
            orientation=power_object.orientation,
            style=power_object.style,
            color=power_object.color,
            font_id=power_object.font_id,
            show_net_name=power_object.show_net_name,
        )


class SheetEntryCodec:
    @staticmethod
    def decode(record: SheetEntryRecord) -> SchematicSheetEntry:
        return SchematicSheetEntry(
            id=f"sheet-entry-{record.index + 1}",
            source_index=record.index,
            source_order=record.index,
            extra_properties={"ll_properties": dict(record.properties)},
            side=record.side,
            distance_from_top=record.distance_from_top,
            name=record.name,
            io_type=record.io_type,
            style=record.style,
            arrow_kind=record.arrow_kind,
            harness_type=record.harness_type,
            harness_color=record.harness_color,
            font_id=record.font_id,
            color=record.color,
            area_color=record.area_color,
            text_color=record.text_color,
            text_style=record.text_style,
            owner_part_id=record.owner_part_id,
            owner_part_display_mode=record.owner_part_display_mode,
        )

    @staticmethod
    def encode(
        entry: SchematicSheetEntry,
        index: int,
        owner_index: int,
    ) -> SheetEntryRecord:
        return SheetEntryRecord(
            index=index,
            owner_index=owner_index,
            owner_part_id=entry.owner_part_id,
            owner_part_display_mode=entry.owner_part_display_mode,
            unique_id=None,
            properties=_ll_properties(entry),
            side=entry.side,
            distance_from_top=entry.distance_from_top,
            name=entry.name,
            io_type=entry.io_type,
            style=entry.style,
            arrow_kind=entry.arrow_kind,
            harness_type=entry.harness_type,
            harness_color=entry.harness_color,
            font_id=entry.font_id,
            color=entry.color,
            area_color=entry.area_color,
            text_color=entry.text_color,
            text_style=entry.text_style,
        )


class SheetSymbolCodec:
    @staticmethod
    def decode(record: SheetSymbolRecord) -> SchematicSheetSymbol:
        return SchematicSheetSymbol(
            id=f"sheet-symbol-{record.index + 1}",
            source_index=record.index,
            source_order=record.index,
            extra_properties={"ll_properties": dict(record.properties)},
            location=record.location,
            x_size=record.x_size,
            y_size=record.y_size,
            is_mirrored=record.is_mirrored,
            file_name=record.file_name,
            sheet_name=record.sheet_name,
            line_width=record.line_width,
            color=record.color,
            area_color=record.area_color,
            is_solid=record.is_solid,
            show_hidden_fields=record.show_hidden_fields,
            symbol_type=record.symbol_type,
        )

    @staticmethod
    def encode(symbol: SchematicSheetSymbol, index: int) -> SheetSymbolRecord:
        return SheetSymbolRecord(
            index=index,
            owner_index=-1,
            owner_part_id=-1,
            owner_part_display_mode=0,
            unique_id=None,
            properties=_ll_properties(symbol),
            location=symbol.location,
            x_size=symbol.x_size,
            y_size=symbol.y_size,
            is_mirrored=symbol.is_mirrored,
            file_name=symbol.file_name,
            sheet_name=symbol.sheet_name,
            line_width=symbol.line_width,
            color=symbol.color,
            area_color=symbol.area_color,
            is_solid=symbol.is_solid,
            show_hidden_fields=symbol.show_hidden_fields,
            symbol_type=symbol.symbol_type,
        )


class PortCodec:
    @staticmethod
    def decode(record: PortRecord) -> SchematicPort:
        return SchematicPort(
            id=f"port-{record.index + 1}",
            source_index=record.index,
            source_order=record.index,
            extra_properties={"ll_properties": dict(record.properties)},
            location=record.location,
            name=record.name,
            io_type=record.io_type,
            style=record.style,
            alignment=record.alignment,
            width=record.width,
            height=record.height,
            border_width=record.border_width,
            auto_size=record.auto_size,
            connected_end=record.connected_end,
            cross_reference=record.cross_reference,
            show_net_name=record.show_net_name,
            harness_type=record.harness_type,
            harness_color=record.harness_color,
            is_custom_style=record.is_custom_style,
            font_id=record.font_id,
            color=record.color,
            area_color=record.area_color,
            text_color=record.text_color,
            owner_part_id=record.owner_part_id,
            owner_part_display_mode=record.owner_part_display_mode,
        )

    @staticmethod
    def encode(port: SchematicPort, index: int, owner_index: int = -1) -> PortRecord:
        return PortRecord(
            index=index,
            owner_index=owner_index,
            owner_part_id=port.owner_part_id,
            owner_part_display_mode=port.owner_part_display_mode,
            unique_id=None,
            properties=_ll_properties(port),
            location=port.location,
            name=port.name,
            io_type=port.io_type,
            style=port.style,
            alignment=port.alignment,
            width=port.width,
            height=port.height,
            border_width=port.border_width,
            auto_size=port.auto_size,
            connected_end=port.connected_end,
            cross_reference=port.cross_reference,
            show_net_name=port.show_net_name,
            harness_type=port.harness_type,
            harness_color=port.harness_color,
            is_custom_style=port.is_custom_style,
            font_id=port.font_id,
            color=port.color,
            area_color=port.area_color,
            text_color=port.text_color,
        )


def _attach_to_component(
    component: SchematicComponent,
    primitive,
) -> None:
    if isinstance(primitive, SchematicPin):
        component.pins.append(primitive)
    elif isinstance(primitive, SchematicParameter):
        component.parameters.append(primitive)
    elif isinstance(primitive, SchematicWire):
        component.wires.append(primitive)
    elif isinstance(primitive, SchematicNetLabel):
        component.net_labels.append(primitive)
    elif isinstance(primitive, SchematicJunction):
        component.junctions.append(primitive)
    elif isinstance(primitive, SchematicPowerObject):
        component.power_objects.append(primitive)


def _attach_to_sheet_symbol(
    sheet_symbol: SchematicSheetSymbol,
    primitive,
) -> None:
    if isinstance(primitive, SchematicSheetEntry):
        sheet_symbol.entries.append(primitive)


def _attach_sheet_symbol_name_metadata(
    sheet_symbol: SchematicSheetSymbol,
    record: UnknownRecord,
) -> bool:
    text = record.properties.get("Text")
    if not isinstance(text, str) or not text:
        return False
    if record.record_type == _SHEET_NAME_RECORD and not sheet_symbol.sheet_name:
        sheet_symbol.sheet_name = text
        return True
    if record.record_type == _SHEET_FILE_NAME_RECORD and not sheet_symbol.file_name:
        sheet_symbol.file_name = text
        return True
    return False


def convert_ll_to_il(doc: AltiumSchDoc) -> AltiumSchematic:
    context = Context(component_by_index={}, sheet_symbol_by_index={})
    components: list[SchematicComponent] = []
    sheet_symbols: list[SchematicSheetSymbol] = []
    for record in doc.records:
        if isinstance(record, ComponentRecord):
            component = ComponentCodec.decode(record)
            context.component_by_index[record.index] = component
            components.append(component)
        elif isinstance(record, SheetSymbolRecord):
            sheet_symbol = SheetSymbolCodec.decode(record)
            context.sheet_symbol_by_index[record.index] = sheet_symbol
            sheet_symbols.append(sheet_symbol)

    schematic = AltiumSchematic(
        header_parameters=dict(doc.header_parameters),
        additional_parameters=dict(doc.additional_parameters),
        storage_data=doc.storage_data,
        components=components,
        sheet_symbols=sheet_symbols,
    )

    for record in doc.records:
        if isinstance(record, ComponentRecord | SheetSymbolRecord):
            continue
        primitive = None
        if isinstance(record, PinRecord):
            primitive = PinCodec.decode(record)
        elif isinstance(record, ParameterRecord):
            primitive = ParameterCodec.decode(record)
        elif isinstance(record, WireRecord):
            primitive = WireCodec.decode(record)
        elif isinstance(record, BusRecord):
            primitive = BusCodec.decode(record)
        elif isinstance(record, NetLabelRecord):
            primitive = NetLabelCodec.decode(record)
        elif isinstance(record, JunctionRecord):
            primitive = JunctionCodec.decode(record)
        elif isinstance(record, PowerObjectRecord):
            primitive = PowerObjectCodec.decode(record)
        elif isinstance(record, SheetEntryRecord):
            primitive = SheetEntryCodec.decode(record)
        elif isinstance(record, PortRecord):
            primitive = PortCodec.decode(record)
        if primitive is None:
            if isinstance(record, UnknownRecord):
                owner_sheet_symbol = context.sheet_symbol_by_index.get(
                    record.owner_index
                )
                if owner_sheet_symbol is not None and (
                    _attach_sheet_symbol_name_metadata(owner_sheet_symbol, record)
                ):
                    continue
            continue
        owner = context.component_by_index.get(record.owner_index)
        if owner is not None:
            _attach_to_component(owner, primitive)
            continue
        owner_sheet_symbol = context.sheet_symbol_by_index.get(record.owner_index)
        if owner_sheet_symbol is not None:
            _attach_to_sheet_symbol(owner_sheet_symbol, primitive)
            continue
        if isinstance(primitive, SchematicParameter):
            schematic.parameters.append(primitive)
        elif isinstance(primitive, SchematicWire):
            schematic.wires.append(primitive)
        elif isinstance(primitive, SchematicBus):
            schematic.buses.append(primitive)
        elif isinstance(primitive, SchematicNetLabel):
            schematic.net_labels.append(primitive)
        elif isinstance(primitive, SchematicJunction):
            schematic.junctions.append(primitive)
        elif isinstance(primitive, SchematicPowerObject):
            schematic.power_objects.append(primitive)
        elif isinstance(primitive, SchematicPort):
            schematic.ports.append(primitive)

    schematic.extra_properties["unsupported_record_count"] = len(doc.unknown_records)
    return schematic


def convert_il_to_ll(schematic: AltiumSchematic) -> AltiumSchDoc:
    records: list[SchematicRecord] = []
    next_index = 0

    for component in schematic.components:
        component_record = ComponentCodec.encode(component, next_index)
        records.append(component_record)
        owner_index = next_index
        next_index += 1
        for pin in component.pins:
            records.append(PinCodec.encode(pin, next_index, owner_index))
            next_index += 1
        for parameter in component.parameters:
            records.append(ParameterCodec.encode(parameter, next_index, owner_index))
            next_index += 1
        for wire in component.wires:
            records.append(WireCodec.encode(wire, next_index, owner_index))
            next_index += 1
        for net_label in component.net_labels:
            records.append(NetLabelCodec.encode(net_label, next_index, owner_index))
            next_index += 1
        for junction in component.junctions:
            records.append(JunctionCodec.encode(junction, next_index, owner_index))
            next_index += 1
        for power_object in component.power_objects:
            records.append(
                PowerObjectCodec.encode(power_object, next_index, owner_index)
            )
            next_index += 1
    for sheet_symbol in schematic.sheet_symbols:
        sheet_symbol_record = SheetSymbolCodec.encode(sheet_symbol, next_index)
        records.append(sheet_symbol_record)
        owner_index = next_index
        next_index += 1
        for entry in sheet_symbol.entries:
            records.append(SheetEntryCodec.encode(entry, next_index, owner_index))
            next_index += 1

    for parameter in schematic.parameters:
        records.append(ParameterCodec.encode(parameter, next_index))
        next_index += 1
    for port in schematic.ports:
        records.append(PortCodec.encode(port, next_index))
        next_index += 1
    for wire in schematic.wires:
        records.append(WireCodec.encode(wire, next_index))
        next_index += 1
    for bus in schematic.buses:
        records.append(BusCodec.encode(bus, next_index))
        next_index += 1
    for net_label in schematic.net_labels:
        records.append(NetLabelCodec.encode(net_label, next_index))
        next_index += 1
    for junction in schematic.junctions:
        records.append(JunctionCodec.encode(junction, next_index))
        next_index += 1
    for power_object in schematic.power_objects:
        records.append(PowerObjectCodec.encode(power_object, next_index))
        next_index += 1

    return AltiumSchDoc(
        header_parameters=dict(schematic.header_parameters),
        additional_parameters=dict(schematic.additional_parameters),
        records=records,
        storage_data=schematic.storage_data,
    )


__all__ = [
    "ComponentCodec",
    "BusCodec",
    "JunctionCodec",
    "NetLabelCodec",
    "ParameterCodec",
    "PinCodec",
    "PortCodec",
    "PowerObjectCodec",
    "SheetEntryCodec",
    "SheetSymbolCodec",
    "WireCodec",
    "convert_il_to_ll",
    "convert_ll_to_il",
]
