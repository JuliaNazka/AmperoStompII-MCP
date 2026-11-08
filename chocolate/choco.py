"""Protocolo do M-Vave Chocolate (FootCtrlPlus), decifrado espionando o CubeSuite V2.8.10 (nov/2026).

SysEx: F0 + bytes de 8 bits empacotados em 7 bits (fluxo de bits, bit menos significativo primeiro) + F7.
Mensagem (8 bits): 00 59 CMD LEN(u16, LE) 00 04 <carga> CHK
  LEN = tamanho da carga + 1 (o CHK);  CHK = 0xFF - soma(bytes a partir do "00 04") & 0xFF
  CMD 0x23 ler:    carga = endereço u32 + n u16 + 00        resposta: mesmo cabeçalho + n bytes de dados
  CMD 0x22 gravar: carga = endereço u32 + n u16 + 00 + dados resposta: 00 59 00 01 00 00 00 FF
A configuração é uma memória de 23646 bytes, igual ao arquivo .fcp que o CubeSuite exporta.

Modo "Advanced custom" (o da Julia): bloco de 417 bytes por pedal em 93 + 1668*grupo + 417*pedal
  (grupo 0..7, pedal 0..3 = A..D)
  +0  modo de pisada (STEP_MODES)
  +1  banco A: quantidade + mensagens de 4 bytes [canal-1, tipo, número, valor]
  +81 banco B: idem       (tipo: 0 PC, 1 CC, 2 Note On, 3 Note Off, 4 MIDI personalizado)
Isso acima é o que o CubeSuite V2.8.10 grava, e o firmware 012 lê (testado pisando, grupo 1 A-D, 02/11/2026;
atualizar o firmware zerou a config). Histórico do firmware 010, que não lia esse formato:
o aparelho respondia "FootCtrlPlus_010" e o firmware lia outro formato (testado pisando, 01/11/2026): bloco de 561 bytes por pedal em 94 + 561*r
(+1 banco A, +81 banco B, mais 5 bancos de 80), cada banco = 16 mensagens de 5 bytes [canal-1, tipo, número,
valor, ?], sem contador (para na primeira mensagem zerada). Pedal A do grupo 1 = r0; B/C/D pareciam r1..r3,
mas a ordem mudou entre rodadas (grupo?) e o byte do modo de pisada não foi achado.
Esse mapa de 561 bytes também está errado: gravar marcas nele trava o aparelho (02/11/2026).
O que funciona de verdade nesse firmware é o modo Custom (testado pisando, 02/11/2026), no começo da memória:
  0   modo do aparelho (0 = PC, 2 = Custom CC, 3 = Advanced custom 1; o visor mostra 000 no Custom)
  2   canal MIDI - 1
  3+2p, 4+2p  pedal p (A..D): alterna (1 = cada pisada alterna 127/0; 0 = 127 ao pisar e 0 ao soltar), número do CC
  1, 11-13: desconhecidos (originais 1, 0, 7, 1); mexer no 1 ou no 13 não mudou nada.
O aparelho guarda o liga/desliga de cada pedal; vale na hora, sem religar. 21950 em diante muda sozinho (área de trabalho).
"""
from __future__ import annotations

import struct

SIZE = 23646
CMD_READ, CMD_WRITE = 0x23, 0x22
ACK = bytes([0x00, 0x59, 0x00, 0x01, 0x00, 0x00, 0x00, 0xFF])

REC0, GROUP, REC = 93, 1668, 417
BANK = 80                  # bytes por banco: quantidade + mensagens
MAX_MSGS = 16              # o banco cabe 19; 16 é o que usamos para não encostar no limite
ADDR_MODE, ADDR_MAXGROUP, ADDR_GROUP = 21950, 23639, 23640   # área de trabalho: não confiáveis
ADDR_DEVICE_MODE, ADDR_CHANNEL, ADDR_CUSTOM = 0, 2, 3
MODE_PC, MODE_CUSTOM, MODE_ADV1 = 0, 2, 3

STEP_MODES = [
    "um banco",              # Single step (single Bank information)
    "alterna A/B",           # Single step (switch between two Banks)
    "pisa A / solta B",      # Press down - release (Two sets of information)
    "pisada longa",          # Long step
    "curta A / longa B",     # Step short or step long
]
TYPES = ["PC", "CC", "NoteOn", "NoteOff", "Custom"]
DEVICE_MODES = {MODE_PC: "PC (troca de patch)", MODE_CUSTOM: "Custom (CC, modo stomp)", MODE_ADV1: "Advanced custom 1"}


def pack7(data: bytes) -> list[int]:
    out, acc, n = [], 0, 0
    for b in data:
        acc |= b << n
        n += 8
        while n >= 7:
            out.append(acc & 0x7F)
            acc >>= 7
            n -= 7
    if n:
        out.append(acc & 0x7F)
    return out


def unpack7(data) -> bytes:
    out, acc, n = bytearray(), 0, 0
    for b in data:
        acc |= b << n
        n += 7
        while n >= 8:
            out.append(acc & 0xFF)
            acc >>= 8
            n -= 8
    return bytes(out)


def message(cmd: int, payload: bytes) -> bytes:
    body = b"\x00\x04" + payload
    chk = (0xFF - sum(body)) & 0xFF
    return bytes([0x00, 0x59, cmd]) + struct.pack("<H", len(payload) + 1) + body + bytes([chk])


def frame(msg: bytes) -> list[int]:
    """Mensagem de 8 bits -> SysEx completo (com F0/F7)."""
    return [0xF0, *pack7(msg), 0xF7]


def read_msg(addr: int, n: int) -> bytes:
    return message(CMD_READ, struct.pack("<IHB", addr, n, 0))


def write_msg(addr: int, data: bytes) -> bytes:
    return message(CMD_WRITE, struct.pack("<IHB", addr, len(data), 0) + data)


def parse_reply(msg: bytes) -> dict | None:
    """Resposta (já desempacotada) -> {cmd, addr, data} ou {cmd: 0} para o ok da gravação."""
    if len(msg) < 8 or msg[:2] != b"\x00\x59":
        return None
    if (sum(msg[5:]) & 0xFF) != 0xFF:
        raise ValueError("soma de verificação errada na resposta do Chocolate")
    if msg[2] == CMD_READ:
        addr, n = struct.unpack_from("<IH", msg, 7)
        return {"cmd": CMD_READ, "addr": addr, "data": msg[14:14 + n]}
    return {"cmd": msg[2]}


# ---- modo Custom (começo da memória) ----

def parse_custom(h: bytes) -> dict:
    """h = memória a partir do endereço 0 (pelo menos 11 bytes)."""
    return {"mode": h[ADDR_DEVICE_MODE], "channel": h[ADDR_CHANNEL] + 1,
            "pedals": {"ABCD"[p]: {"cc": h[ADDR_CUSTOM + 1 + 2 * p], "toggle": bool(h[ADDR_CUSTOM + 2 * p])}
                       for p in range(4)}}


def build_custom(h: bytes, channel: int, pedals: dict) -> bytes:
    """Devolve os bytes 0..10 com modo Custom, canal e pedais trocados; o resto (byte 1) fica como estava.
    pedals = {"A": (cc, toggle), ...}; pedal ausente fica como estava."""
    if not 1 <= channel <= 16:
        raise ValueError("canal vai de 1 a 16")
    b = bytearray(h[:ADDR_CUSTOM + 8])
    b[ADDR_DEVICE_MODE] = MODE_CUSTOM
    b[ADDR_CHANNEL] = channel - 1
    for k, (cc, toggle) in pedals.items():
        p = "ABCD".index(k)
        if not 0 <= cc <= 127:
            raise ValueError(f"CC fora da faixa no pedal {k}: {cc}")
        b[ADDR_CUSTOM + 2 * p] = 1 if toggle else 0
        b[ADDR_CUSTOM + 1 + 2 * p] = cc
    return bytes(b)


# ---- blocos do Advanced custom (formato do CubeSuite; vale no firmware 012, o 010 não lia) ----

def rec_addr(group: int, pedal: int) -> int:
    return REC0 + GROUP * group + REC * pedal


def parse_bank(b: bytes) -> list[dict]:
    out = []
    for i in range(min(b[0], 19)):
        ch, t, num, val = b[1 + 4 * i:5 + 4 * i]
        out.append({"type": TYPES[t] if t < len(TYPES) else t, "channel": ch + 1, "number": num, "value": val})
    return out


def build_bank(msgs: list[dict]) -> bytes:
    if len(msgs) > MAX_MSGS:
        raise ValueError(f"no máximo {MAX_MSGS} mensagens por banco")
    b = bytearray(BANK)
    b[0] = len(msgs)
    for i, m in enumerate(msgs):
        t = m.get("type", "CC")
        t = TYPES.index(t) if isinstance(t, str) else int(t)
        ch, num, val = int(m.get("channel", 1)), int(m["number"]), int(m.get("value", 0))
        if not (1 <= ch <= 16 and 0 <= num <= 127 and 0 <= val <= 127):
            raise ValueError(f"mensagem fora da faixa: {m}")
        b[1 + 4 * i:5 + 4 * i] = bytes([ch - 1, t, num, val])
    return bytes(b)


def parse_record(r: bytes) -> dict:
    m = r[0]
    return {"step_mode": STEP_MODES[m] if m < len(STEP_MODES) else m,
            "bank_a": parse_bank(r[1:1 + BANK]), "bank_b": parse_bank(r[1 + BANK:1 + 2 * BANK])}
