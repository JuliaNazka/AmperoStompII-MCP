"""Protocolo SysEx da Hotone Ampero II (decifrado espionando o editor oficial, out/2026).

Quadro:  F0 21 25 4D 50 00 00 CHK OP Nlo Nhi OFFlo OFFhi <nibbles> F7
  OP   0x11 = pedido de leitura, 0x12 = escrita / resposta
  N    tamanho total da mensagem interna (7 bits + 7 bits); OFF = posição deste pedaço
  dados: cada byte vira 2 nibbles (alto, baixo); pedaços de até 185 bytes
  CHK  soma dos nibbles do pedaço, & 0x7F
Mensagem interna: cmd (u32 LE) + tamanho (u32 LE) + [0x11 + n] + carga(n) + 11 00 00
  cmd 0x03000001 cena (carga: 1 byte)          cmd 0x01040003 parâmetro (slot u16, id u16, float)
  cmd 0x01040002 modelo (slot, módulo, código u32, 1)   cmd 0x01090004 liga/desliga (cena, 12 bytes)
  cmd 0x01000000 dump do patch (comprimido em LZO1X, 5072 bytes em claro)
  cmd 0x05000000 salvar (índice u32 + nome em 17 bytes, até 16 letras; grava o patch em edição naquele slot)
  cmd 0x00090000 escrito (op 0x12) com índice u32 = selecionar patch
"""
from __future__ import annotations

import struct

from lzo import lzo1x_decompress

HEAD = [0x21, 0x25, 0x4D, 0x50, 0x00, 0x00]
CHUNK = 185

CMD_SCENE = 0x03000001
CMD_MODEL = 0x01040002
CMD_PARAM = 0x01040003
CMD_ONOFF = 0x01090004
CMD_PATCH = 0x01000000
CMD_SAVE = 0x05000000
CMD_CURRENT = 0x00090000


def inner(cmd: int, payload: bytes | None) -> bytes:
    if payload is None:
        return struct.pack("<II", cmd, 0)
    body = bytes([0x11 + len(payload)]) + payload + b"\x11\x00\x00"
    return struct.pack("<II", cmd, len(body)) + body


def frames(msg: bytes, op: int = 0x12) -> list[list[int]]:
    """Mensagem interna -> lista de quadros SysEx (com F0/F7)."""
    out = []
    total = len(msg)
    for off in range(0, max(total, 1), CHUNK):
        part = msg[off:off + CHUNK]
        nib = []
        for b in part:
            nib += [b >> 4, b & 0xF]
        chk = sum(nib) & 0x7F
        out.append([0xF0, *HEAD, chk, op, total & 0x7F, total >> 7, off & 0x7F, off >> 7, *nib, 0xF7])
    return out


def parse_frame(data: list[int]) -> dict | None:
    """Quadro recebido (sem F0/F7, como o mido entrega) -> pedaço."""
    if len(data) < 12 or list(data[:6]) != HEAD:
        return None
    nib = data[12:]
    part = bytes((nib[i] << 4) | nib[i + 1] for i in range(0, len(nib) - 1, 2))
    return {"op": data[7], "total": data[8] | (data[9] << 7), "off": data[10] | (data[11] << 7), "data": part}


class Assembler:
    """Junta pedaços até completar a mensagem interna."""

    def __init__(self):
        self.buf: dict[int, bytes] = {}
        self.total = None

    def feed(self, data: list[int]) -> tuple[int, bytes] | None:
        f = parse_frame(data)
        if not f:
            return None
        if f["off"] == 0:
            self.buf, self.total = {}, f["total"]
        if self.total is None:
            return None
        self.buf[f["off"]] = f["data"]
        got = sum(len(v) for v in self.buf.values())
        if got >= self.total:
            msg = b"".join(self.buf[k] for k in sorted(self.buf))[: self.total]
            self.buf, self.total = {}, None
            return f["op"], msg
        return None


def split_inner(msg: bytes) -> tuple[int, bytes]:
    cmd, ln = struct.unpack_from("<II", msg, 0)
    return cmd, msg[8:8 + ln]


# ---- comandos ----

def scene_msg(scene: int) -> bytes:
    return inner(CMD_SCENE, bytes([scene]))


def param_msg(slot_id: int, param_id: int, value: float) -> bytes:
    return inner(CMD_PARAM, struct.pack("<HHf", slot_id, param_id, value))


def model_msg(slot_id: int, module: int, code: int) -> bytes:
    return inner(CMD_MODEL, struct.pack("<BBIB", slot_id, module, code, 1))


def onoff_msg(scene: int, states: list[int]) -> bytes:
    return inner(CMD_ONOFF, bytes([scene, *states]))


def save_msg(index: int, name: str) -> bytes:
    """"Salvar como" do editor: grava o patch em edição no slot index com esse nome."""
    return inner(CMD_SAVE, struct.pack("<I", index) + name.encode("ascii", "ignore")[:16].ljust(17, b"\0"))


def select_msg(index: int) -> bytes:
    return inner(CMD_CURRENT, struct.pack("<I", index))


# ---- patch ----

def decode_patch_dump(body: bytes) -> bytes:
    """Corpo da mensagem cmd 0x01000000 -> patch em claro (5072 bytes)."""
    return lzo1x_decompress(body)


def parse_patch(p: bytes) -> dict:
    name = p[34:66].split(b"\0")[0].decode("utf-8", "replace")
    style = p[121:131].split(b"\0")[0].decode("utf-8", "replace")
    order = list(p[169:181])
    modules = list(p[181:193])
    codes = [struct.unpack_from("<I", p, 193 + 4 * i)[0] for i in range(12)]
    # knobs: 3 cenas x 12 slots x 25 floats (valores ao vivo, incluindo edições não salvas)
    params = [[list(struct.unpack_from("<25f", p, 249 + 1200 * s + 100 * i)) for i in range(12)] for s in range(3)]
    onoff = [list(p[3849 + 12 * s:3861 + 12 * s]) for s in range(3)]
    scenes = [p[4214 + 8 * s:4222 + 8 * s].split(b"\0")[0].decode("utf-8", "replace") for s in range(3)]
    return {"index": p[0], "name": name, "style": style, "order": order, "modules": modules,
            "codes": codes, "params": params, "onoff": onoff, "scene_names": scenes}
