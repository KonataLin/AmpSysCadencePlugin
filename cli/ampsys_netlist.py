#!/usr/bin/env python3
"""Netlist parsing helpers for the AmpSys Cadence plugin.

The SKILL extractor writes a compact CDL/SPICE-like netlist.  This module
turns that text into the JSON records used by the GUI and runner.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


UNIT_SCALE = {
    "t": 1e12,
    "g": 1e9,
    "meg": 1e6,
    "k": 1e3,
    "": 1.0,
    "m": 1e-3,
    "u": 1e-6,
    "n": 1e-9,
    "p": 1e-12,
    "f": 1e-15,
}

REQUIRED_NODE_NAMES = ("VDD", "GND", "Vin", "Vout")


@dataclass
class DeviceRecord:
    name: str
    kind: str
    nodes: List[str]
    model: str = ""
    value: Optional[float] = None
    current: Optional[float] = None
    match_group: str = ""
    params: Dict[str, str] = None
    raw_nodes: List[str] = None
    terminal_order: str = ""

    def to_json(self) -> Dict:
        payload = asdict(self)
        payload["type"] = payload.pop("kind")
        return payload


def parse_number(value: str) -> Optional[float]:
    text = str(value).strip().strip('"').strip("'")
    if not text:
        return None
    match = re.fullmatch(r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)([a-zA-Z]*)", text)
    if not match:
        return None
    base = float(match.group(1))
    suffix = match.group(2).lower()
    if suffix not in UNIT_SCALE:
        return None
    return base * UNIT_SCALE[suffix]


def normalize_model_set(items: Iterable[str]) -> set:
    return {str(x).strip().lower() for x in items if str(x).strip()}


TERMINAL_ALIASES = {
    "D": "D",
    "DRAIN": "D",
    "G": "G",
    "GATE": "G",
    "S": "S",
    "SOURCE": "S",
    "B": "B",
    "BULK": "B",
    "BODY": "B",
    "SUB": "B",
    "SUBSTRATE": "B",
}


def normalize_terminal_order(value: str) -> List[str]:
    tokens = [x.strip().upper() for x in re.split(r"[\s,;/|]+", str(value or "")) if x.strip()]
    roles = [TERMINAL_ALIASES.get(tok, tok) for tok in tokens]
    if len(roles) != 4 or set(roles) != {"D", "G", "S", "B"}:
        return ["D", "G", "S", "B"]
    return roles


def apply_terminal_order(nodes: List[str], order_value: str) -> Tuple[List[str], str]:
    order = normalize_terminal_order(order_value)
    mapped = {role: nodes[idx] for idx, role in enumerate(order[:4])}
    return [mapped["D"], mapped["G"], mapped["S"], mapped["B"]], " ".join(order)


def parse_param_tokens(tokens: List[str]) -> Tuple[List[str], Dict[str, str]]:
    plain: List[str] = []
    params: Dict[str, str] = {}
    for tok in tokens:
        if "=" in tok:
            key, val = tok.split("=", 1)
            params[key.strip().lower()] = val.strip()
        else:
            plain.append(tok)
    return plain, params


def logical_spice_lines(text: str) -> Iterable[str]:
    current = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("*") or line.startswith("//"):
            continue
        if line.startswith("+"):
            extra = line[1:].strip()
            current = f"{current} {extra}".strip() if current else extra
            continue
        if current:
            yield current
        current = line
    if current:
        yield current


def classify_instance(
    first_token: str,
    plain_tokens: List[str],
    params: Dict[str, str],
    nmos_models: set,
    pmos_models: set,
    terminal_orders: Optional[Dict[str, str]] = None,
) -> Optional[DeviceRecord]:
    if not first_token or first_token.startswith("."):
        return None

    prefix = first_token[0].upper()
    if prefix == "M" or len(plain_tokens) >= 5:
        if len(plain_tokens) < 5:
            return None
        raw_nodes = plain_tokens[:4]
        nodes = list(raw_nodes)
        model = plain_tokens[4]
        model_key = model.lower()
        if model_key in pmos_models or model_key in {"pmos", "pch", "pfet", "p"}:
            kind = "pmos"
        elif model_key in nmos_models or model_key in {"nmos", "nch", "nfet", "n"}:
            kind = "nmos"
        elif prefix != "M":
            return None
        else:
            kind = "unknown_mos"
        term_order = ""
        if kind in {"nmos", "pmos"}:
            nodes, term_order = apply_terminal_order(raw_nodes, (terminal_orders or {}).get(kind, "D G S B"))
        return DeviceRecord(
            name=first_token,
            kind=kind,
            nodes=nodes,
            model=model,
            params=params,
            raw_nodes=raw_nodes,
            terminal_order=term_order,
        )

    if prefix in {"R", "C"} and len(plain_tokens) >= 3:
        kind = "res" if prefix == "R" else "cap"
        value = parse_number(plain_tokens[2])
        return DeviceRecord(
            name=first_token,
            kind=kind,
            nodes=plain_tokens[:2],
            model=kind,
            value=value,
            params=params,
        )

    return None


def parse_netlist(
    path: Path,
    nmos_models: Iterable[str] = (),
    pmos_models: Iterable[str] = (),
    terminal_orders: Optional[Dict[str, str]] = None,
) -> Tuple[List[str], List[DeviceRecord], List[str]]:
    nmos_set = normalize_model_set(nmos_models)
    pmos_set = normalize_model_set(pmos_models)
    pins: List[str] = []
    devices: List[DeviceRecord] = []
    warnings: List[str] = []

    for line in logical_spice_lines(path.read_text(encoding="utf-8", errors="ignore")):
        lower = line.lower()
        if lower.startswith(".subckt"):
            parts = line.split()
            pins = parts[2:]
            continue
        if lower.startswith(".ends") or lower.startswith(".include") or lower.startswith(".lib"):
            continue

        tokens = line.split()
        if not tokens:
            continue
        first = tokens[0]
        plain, params = parse_param_tokens(tokens[1:])
        rec = classify_instance(first, plain, params, nmos_set, pmos_set, terminal_orders)
        if rec is not None:
            devices.append(rec)

    for rec in devices:
        if rec.kind == "unknown_mos":
            warnings.append(
                f"{rec.name}: model '{rec.model}' was not listed as NMOS/PMOS. "
                "Set model names in Library Builder before running."
            )
        if rec.kind in {"nmos", "pmos"} and len(rec.nodes) != 4:
            warnings.append(f"{rec.name}: MOS node count is not 4, expected D G S B.")

    all_nodes = {n for rec in devices for n in rec.nodes}
    visible_nodes = all_nodes | set(pins)
    for name in REQUIRED_NODE_NAMES:
        if name not in visible_nodes:
            warnings.append(f"Required node '{name}' was not found. Net names must include VDD, GND, Vin, Vout.")

    return pins, devices, warnings


def parse_to_json(
    netlist: str,
    output: str,
    nmos: str = "",
    pmos: str = "",
    nmos_terminal_order: str = "D G S B",
    pmos_terminal_order: str = "D G S B",
) -> None:
    pins, devices, warnings = parse_netlist(
        Path(netlist),
        [x.strip() for x in nmos.split(",")],
        [x.strip() for x in pmos.split(",")],
        {"nmos": nmos_terminal_order, "pmos": pmos_terminal_order},
    )
    payload = {
        "netlist": str(Path(netlist).resolve()),
        "pins": pins,
        "devices": [d.to_json() for d in devices],
        "warnings": warnings,
    }
    Path(output).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Parse a Cadence-exported netlist for AmpSys.")
    parser.add_argument("--netlist", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--nmos", default="")
    parser.add_argument("--pmos", default="")
    parser.add_argument("--nmos-terminal-order", default="D G S B")
    parser.add_argument("--pmos-terminal-order", default="D G S B")
    args = parser.parse_args()
    parse_to_json(args.netlist, args.output, args.nmos, args.pmos, args.nmos_terminal_order, args.pmos_terminal_order)


if __name__ == "__main__":
    main()
