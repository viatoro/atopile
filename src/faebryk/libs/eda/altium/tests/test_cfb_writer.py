# This file is part of the faebryk project
# SPDX-License-Identifier: MIT

from __future__ import annotations

import struct

from aaf2.cfb import CompoundFileBinary

from faebryk.libs.eda.altium.lib.cfb_writer import CfbWriter


def test_cfb_writer_writes_ole_v3_streams(tmp_path):
    output_path = tmp_path / "test.PcbDoc"
    streams = {
        "Board6/Data": b"board",
        "Tracks6/Data": b"x" * 5000,
    }

    writer = CfbWriter()
    for path, data in streams.items():
        writer.add_stream(path, data)
    writer.write(output_path)

    raw = output_path.read_bytes()

    assert raw[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    assert raw[8:24] == bytes(16)
    assert struct.unpack_from("<H", raw, 24)[0] == 0x003E
    assert struct.unpack_from("<H", raw, 26)[0] == 3
    assert struct.unpack_from("<H", raw, 30)[0] == 9
    assert struct.unpack_from("<I", raw, 40)[0] == 0
    assert struct.unpack_from("<I", raw, 52)[0] == 0

    with output_path.open("rb") as handle:
        cfb = CompoundFileBinary(handle, mode="rb")

        assert cfb.root.class_id is None
        assert (
            bytes(cfb.open("/Board6/Data", mode="r").read()) == streams["Board6/Data"]
        )
        assert (
            bytes(cfb.open("/Tracks6/Data", mode="r").read()) == streams["Tracks6/Data"]
        )
