# This file is part of the faebryk project
# SPDX-License-Identifier: MIT

import unittest

import faebryk.library._F as F  # noqa: F401
from faebryk.exporters.pcb.kicad.transformer import PCB_Transformer
from faebryk.libs.kicad.fileformats import Property, kicad
from faebryk.libs.test.fileformats import FPFILE, PCBFILE
from faebryk.libs.util import find


class TestTransformer(unittest.TestCase):
    def test_bbox(self):
        pcb = kicad.loads(kicad.pcb.PcbFile, PCBFILE)
        fp = find(
            pcb.kicad_pcb.footprints,
            lambda f: Property.get_property(f.propertys, "Reference") == "R1",
        )
        bbox_pads = PCB_Transformer.get_footprint_pads_bbox(fp)
        self.assertEqual(bbox_pads, ((-0.715, -0.27), (0.715, 0.27)))

        bbox_silk = PCB_Transformer.get_footprint_silkscreen_bbox(fp)
        self.assertEqual(bbox_silk, ((-0.94, -0.5), (0.94, 0.5)))

    def test_fp_common_fields_normalize_tags_for_pcb_footprint(self):
        lib_fp = kicad.footprint.loads(FPFILE.read_text()).footprint
        lib_fp.name = "LED_0201_0603Metric"

        attrs = PCB_Transformer._fp_common_fields_dict(
            object.__new__(PCB_Transformer),
            lib_fp,
        )

        self.assertEqual(attrs["tags"], ["LED"])

        footprint = kicad.pcb.Footprint(
            at=kicad.pcb.Xyr(x=0, y=0, r=0),
            **attrs,
        )

        self.assertEqual(footprint.tags, ["LED"])
