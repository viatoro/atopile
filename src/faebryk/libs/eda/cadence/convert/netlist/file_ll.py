"""Cadence/OrCAD netlist file -> LL codec.

Supports the component-centric OrCAD PCB II text format used by the local
Altium-generated reference netlists, for example:

    ( {OrCAD PCB II Netlist Format}
     ( 00000009 C0603 C1 100nF
      ( 1 NetC1_1 )
      ( 2 NetC1_2 )
     )
    )
"""

from __future__ import annotations

import re
from pathlib import Path

from faebryk.libs.eda.cadence.models.netlist import ll as cadence_netlist_ll

_REFDES_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9?._+]*$")
_TYPICAL_REFDES_RE = re.compile(r"^[A-Za-z]{1,4}\d{1,3}[A-Za-z]?$")
_PACKAGEISH_RE = re.compile(r"^[A-Za-z]{2,8}\d{2,}[A-Za-z0-9._]*$")
_PIN_LINE_RE = re.compile(r"^\(\s*(?P<pin>\S+)\s+(?P<net>\S+)\s*\)$")
_HEADER_LINE_RE = re.compile(r"^\(\s*(?P<payload>\S.*)$")
_FORMAT_LINE_RE = re.compile(r"^\(\s*\{(?P<name>[^}]+)\}\s*$")
_COMMON_REFDES_PREFIXES = {
    "ANT",
    "BAT",
    "BZ",
    "C",
    "CN",
    "D",
    "DS",
    "F",
    "FD",
    "H",
    "ICSP",
    "J",
    "JP",
    "K",
    "L",
    "LED",
    "LOGO",
    "M",
    "P",
    "Q",
    "R",
    "RN",
    "S",
    "SW",
    "T",
    "TP",
    "U",
    "X",
    "Y",
}


def _refdes_prefix(token: str) -> str:
    prefix_chars: list[str] = []
    for char in token:
        if not char.isalpha():
            break
        prefix_chars.append(char.upper())
    return "".join(prefix_chars)


def _score_refdes_candidate(token: str) -> int:
    if not token or not _REFDES_RE.fullmatch(token):
        return -100

    score = 0
    if token[0].isalpha():
        score += 5
    elif any(char.isalpha() for char in token):
        score += 4
    else:
        score -= 10

    if any(char.isdigit() for char in token):
        score += 4
    if token.endswith("?"):
        score += 5
    elif "?" in token:
        score += 2
    if token.isupper() and len(token) <= 8:
        score += 1
    if _TYPICAL_REFDES_RE.fullmatch(token):
        score += 4

    prefix = _refdes_prefix(token)
    if prefix in _COMMON_REFDES_PREFIXES:
        score += 3
    if prefix not in _COMMON_REFDES_PREFIXES and _PACKAGEISH_RE.fullmatch(token):
        score -= 6

    if "(" in token or ")" in token or "," in token or '"' in token:
        score -= 4

    return score


def _split_component_header(
    payload: str,
) -> tuple[str, str, str, str]:
    tokens = payload.split()
    if len(tokens) < 2:
        raise ValueError(f"invalid component header line: {payload!r}")

    record_id = tokens[0]
    if len(tokens) == 2:
        return (record_id, "", tokens[1], "")
    if len(tokens) == 3:
        return (record_id, tokens[1], tokens[2], "")

    candidate_end = len(tokens)
    if len(tokens) > 3:
        candidate_end -= 1
    # The first token after the record id is always part of the footprint/header
    # payload in the Altium-generated OrCAD PCB II files we ingest. Treating it as
    # a refdes breaks cases like `TP 1V2_ TP` and `PAD07 12VH Test point`, where
    # the package name is short and superficially refdes-shaped.
    candidate_indices = range(2, candidate_end)

    best_index = max(
        candidate_indices,
        key=lambda index: (
            _score_refdes_candidate(tokens[index])
            + (4 if index < len(tokens) - 1 and tokens[index] == tokens[-1] else 0),
            index,
        ),
    )
    if _score_refdes_candidate(tokens[best_index]) < 0:
        best_index = len(tokens) - 1 if len(tokens) <= 3 else len(tokens) - 2

    footprint = " ".join(tokens[1:best_index]).strip()
    refdes = tokens[best_index]
    value = " ".join(tokens[best_index + 1 :]).strip()
    return (record_id, footprint, refdes, value)


class NetlistCodec:
    """Read a Cadence/OrCAD netlist text file and produce an LL Netlist."""

    @staticmethod
    def read(path: Path) -> cadence_netlist_ll.Netlist:
        text = path.read_text(encoding="utf-8", errors="replace")
        return NetlistCodec.decode(text)

    @staticmethod
    def decode(text: str) -> cadence_netlist_ll.Netlist:
        format_name: str | None = None
        components: list[cadence_netlist_ll.Component] = []
        current_component: cadence_netlist_ll.Component | None = None

        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line == ")":
                current_component = None
                continue

            if current_component is not None:
                pin_match = _PIN_LINE_RE.fullmatch(line)
                if pin_match is not None:
                    current_component.pins.append(
                        cadence_netlist_ll.ComponentPin(
                            pin=pin_match.group("pin"),
                            net_name=pin_match.group("net"),
                        )
                    )
                    continue

            format_match = _FORMAT_LINE_RE.fullmatch(line)
            if format_match is not None:
                format_name = format_match.group("name").strip()
                continue

            header_match = _HEADER_LINE_RE.fullmatch(line)
            if header_match is None:
                continue

            record_id, footprint, refdes, value = _split_component_header(
                header_match.group("payload")
            )
            current_component = cadence_netlist_ll.Component(
                record_id=record_id,
                footprint=footprint,
                refdes=refdes,
                value=value,
                raw_header=header_match.group("payload"),
            )
            components.append(current_component)

        return cadence_netlist_ll.Netlist(
            format_name=format_name,
            components=components,
        )
