# This file is part of the faebryk project
# SPDX-License-Identifier: MIT

"""
JSON stackup exporter.

Generates a JSON representation of the PCB stackup with layer properties,
manufacturer info, and material data.
"""

import json
import logging
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import faebryk.core.node as fabll
import faebryk.library._F as F
from faebryk.library.PCBManufacturing import PCBLayer

logger = logging.getLogger(__name__)


@dataclass
class StackupLayer:
    """A single layer in the PCB stackup."""

    index: int
    layerType: F.PCBManufacturing.PCBLayer.LayerType
    material: F.PCBManufacturing.PCBLayer.Material
    thicknessMm: float | None
    relativePermittivity: float | None
    lossTangent: float | None


@dataclass
class StackupManufacturer:
    """Manufacturer information for the stackup."""

    name: str
    country: str | None
    website: str | None = None


@dataclass
class JSONStackupOutput:
    """The full JSON stackup output."""

    version: str = "1.0"
    stackupName: str | None = None
    manufacturer: StackupManufacturer | None = None
    layers: list[StackupLayer] = field(default_factory=list)
    layerCount: int = 0
    totalThicknessMm: float | None = None

    def to_dict(self) -> dict:
        def _enum_to_str(obj: Any) -> Any:
            """Convert dataclass dict, turning enums into lowercase name strings."""
            if isinstance(obj, dict):
                return {k: _enum_to_str(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_enum_to_str(v) for v in obj]
            if isinstance(obj, Enum):
                return obj.name.lower()
            return obj

        return _enum_to_str(asdict(self))

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


def _safe_get_thickness_mm(layer: PCBLayer) -> float | None:
    """Get layer thickness in mm, or None if not set."""
    try:
        thickness_m = layer.thickness.get().force_extract_superset().get_single()
        return round(thickness_m * 1000, 4)
    except Exception:
        return None


def _safe_get_numeric(param: F.Parameters.NumericParameter) -> float | None:
    """Get numeric parameter value, or None if not set."""
    try:
        return round(param.force_extract_superset().get_single(), 4)
    except Exception:
        return None


def export_stackup_json(app: fabll.Node, path: Path) -> None:
    """
    Export the PCB stackup as a JSON file.

    Finds the is_pcb trait on the app, retrieves the stackup layers,
    and writes a JSON file with layer properties.
    """
    board_nodes = app.get_children(
        direct_only=False,
        types=fabll.Node,
        required_trait=F.PCBManufacturing.is_pcb,
        include_root=True,
    )
    stackup_nodes = app.get_children(
        direct_only=False,
        types=fabll.Node,
        required_trait=F.PCBManufacturing.is_pcb_stackup,
        include_root=True,
    )

    has_pcb = bool(board_nodes)
    has_stackup = bool(stackup_nodes)

    if has_stackup and not has_pcb:
        logger.warning(
            "Stackup found but no board definition (trait is_pcb), "
            "skipping stackup export"
        )
        return
    if has_pcb and not has_stackup:
        logger.warning("Board found but no stackup defined, skipping stackup export")
        return
    if not has_pcb and not has_stackup:
        return

    board = board_nodes[0].get_trait(F.PCBManufacturing.is_pcb)
    stackup_trait = board.get_stackup()
    layers = stackup_trait.get_layers()

    if not layers:
        logger.warning("Stackup has no layers, skipping stackup export")
        return

    stackup_name = (
        st.split("::")[-1]
        if (st := stackup_trait.get_stackup().get_type_name())
        else None
    )

    # Find manufacturer info from the stackup's children
    stackup_node = stackup_trait.get_stackup()
    manufacturer_nodes = stackup_node.get_children(
        direct_only=False,
        types=fabll.Node,
        required_trait=F.PCBManufacturing.is_company,
    )

    manufacturer = None
    if manufacturer_nodes:
        company = manufacturer_nodes[0].get_trait(F.PCBManufacturing.is_company)
        try:
            name = company.get_company_name()
        except Exception:
            name = "Unknown"
        try:
            website = company.get_website()
        except Exception:
            website = None
        try:
            country = company.get_country_code()
            country_str = country.name if country else None
        except Exception:
            country_str = None

        manufacturer = StackupManufacturer(
            name=name,
            country=country_str,
            website=website,
        )

    stackup_layers = []
    for i, layer in enumerate(layers):
        stackup_layers.append(
            StackupLayer(
                index=i,
                layerType=layer.layer_type.get().force_extract_singleton_typed(
                    F.PCBManufacturing.PCBLayer.LayerType
                ),
                material=layer.material.get().force_extract_singleton_typed(
                    F.PCBManufacturing.PCBLayer.Material
                ),
                thicknessMm=_safe_get_thickness_mm(layer),
                relativePermittivity=_safe_get_numeric(
                    layer.relative_permittivity.get()
                ),
                lossTangent=_safe_get_numeric(layer.loss_tangent.get()),
            )
        )

    copper_count = sum(
        1
        for sl in stackup_layers
        if sl.layerType == F.PCBManufacturing.PCBLayer.LayerType.COPPER
    )
    thicknesses = [
        sl.thicknessMm for sl in stackup_layers if sl.thicknessMm is not None
    ]
    total_thickness = round(sum(thicknesses), 4) if thicknesses else None

    output = JSONStackupOutput(
        stackupName=stackup_name,
        manufacturer=manufacturer,
        layers=stackup_layers,
        layerCount=copper_count,
        totalThicknessMm=total_thickness,
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(output.to_json())
    logger.info(f"Exported stackup to {path}")


# --- Tests ---


class TestStackupJSON:
    @staticmethod
    def test_empty_stackup_serializes():
        output = JSONStackupOutput()
        d = output.to_dict()
        assert d == {
            "version": "1.0",
            "stackupName": None,
            "manufacturer": None,
            "layers": [],
            "layerCount": 0,
            "totalThicknessMm": None,
        }
        parsed = json.loads(output.to_json())
        assert parsed == d

    @staticmethod
    def test_full_stackup_serializes():
        output = JSONStackupOutput(
            stackupName="JLCPCB_4Layer_1_6mm",
            manufacturer=StackupManufacturer(
                name="JLCPCB",
                country="CN",
                website="https://jlcpcb.com",
            ),
            layers=[
                StackupLayer(
                    index=0,
                    layerType=F.PCBManufacturing.PCBLayer.LayerType.COPPER,
                    material=F.PCBManufacturing.PCBLayer.Material.COPPER,
                    thicknessMm=0.035,
                    relativePermittivity=None,
                    lossTangent=None,
                ),
                StackupLayer(
                    index=1,
                    layerType=F.PCBManufacturing.PCBLayer.LayerType.SUBSTRATE,
                    material=F.PCBManufacturing.PCBLayer.Material.FR4,
                    thicknessMm=0.2,
                    relativePermittivity=4.5,
                    lossTangent=0.02,
                ),
            ],
            layerCount=1,
            totalThicknessMm=0.235,
        )

        parsed = json.loads(output.to_json())

        assert parsed["version"] == "1.0"
        assert parsed["stackupName"] == "JLCPCB_4Layer_1_6mm"
        assert parsed["manufacturer"]["name"] == "JLCPCB"
        assert parsed["manufacturer"]["country"] == "CN"
        assert parsed["manufacturer"]["website"] == "https://jlcpcb.com"
        assert len(parsed["layers"]) == 2
        assert parsed["layers"][0]["layerType"] == "copper"
        assert parsed["layers"][0]["thicknessMm"] == 0.035
        assert parsed["layers"][1]["relativePermittivity"] == 4.5
        assert parsed["layers"][1]["lossTangent"] == 0.02
        assert parsed["layerCount"] == 1
        assert parsed["totalThicknessMm"] == 0.235

    @staticmethod
    def test_none_fields_are_json_null():
        output = JSONStackupOutput(
            layers=[
                StackupLayer(
                    index=0,
                    layerType=F.PCBManufacturing.PCBLayer.LayerType.COPPER,
                    material=F.PCBManufacturing.PCBLayer.Material.COPPER,
                    thicknessMm=None,
                    relativePermittivity=None,
                    lossTangent=None,
                ),
            ],
        )
        parsed = json.loads(output.to_json())
        layer = parsed["layers"][0]
        assert layer["thicknessMm"] is None
        assert layer["relativePermittivity"] is None
        assert layer["lossTangent"] is None

    @staticmethod
    def test_writes_to_file(tmp_path: Path):
        output = JSONStackupOutput(
            stackupName="Test",
            layers=[
                StackupLayer(
                    index=0,
                    layerType=F.PCBManufacturing.PCBLayer.LayerType.COPPER,
                    material=F.PCBManufacturing.PCBLayer.Material.COPPER,
                    thicknessMm=0.035,
                    relativePermittivity=None,
                    lossTangent=None,
                ),
            ],
        )

        out_file = tmp_path / "stackup.json"
        out_file.write_text(output.to_json())

        parsed = json.loads(out_file.read_text())
        assert parsed["stackupName"] == "Test"
        assert len(parsed["layers"]) == 1

    @staticmethod
    def test_manufacturer_without_website():
        output = JSONStackupOutput(
            manufacturer=StackupManufacturer(
                name="PCBWay",
                country="CN",
            ),
        )
        parsed = json.loads(output.to_json())
        assert parsed["manufacturer"]["name"] == "PCBWay"
        assert parsed["manufacturer"]["website"] is None
