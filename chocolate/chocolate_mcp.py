"""MCP para o controlador M-Vave Chocolate (aparece no Windows como "FootCtrlPlus").

Lê e grava a configuração dos pedais (modo Advanced custom) pela USB, como o CubeSuite faz,
e escuta o que cada pisada manda. Protocolo em choco.py.
O CubeSuite pode ficar aberto, mas só mostra as mudanças depois de fechar e abrir a janela do Chocolate.
"""
from __future__ import annotations

import os
import re
import sys
import threading
import time

import mido
from mcp.server import MCPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import choco  # noqa: E402

mcp = MCPServer("chocolate", instructions=(
    "Configura o controlador M-Vave Chocolate Plus (firmware 012) pela USB; a chave dele tem que estar em U. "
    "A config da Julia é o modo Advanced custom 1: use read_buttons e set_button (um pedal por vez, várias "
    "mensagens por pisada). O modo Custom (read_stomp/set_stomp) é mais simples, um CC por pedal, mas set_stomp "
    "tira o aparelho do Advanced custom: confirme antes. Para a Hotone Ampero II Stomp: CC 48-59 liga/desliga os slots "
    "A1-B6 (127 liga, 0 desliga), CC 60 afinador, CC 76 tap, CC 26/27 patch -/+; o canal tem que bater com o canal "
    "de entrada da Stomp (5). Se o Chocolate não responder, peça para tirar e recolocar o cabo USB."))

PORT_HINT = "FootCtrl"
BACKUPS = os.path.join(HERE, "backups")
CHUNK = 512          # leitura em pedaços: respostas maiores estouram o buffer de SysEx do Windows
WORK_AREA = 20318    # dali em diante é área de trabalho do aparelho (muda sozinha); restore não mexe
_lock = threading.Lock()
_ports: dict[str, object] = {}


def _port(kind: str):
    p = _ports.get(kind)
    if p is None or p.closed:
        names = mido.get_output_names() if kind == "out" else mido.get_input_names()
        name = next((n for n in names if PORT_HINT.lower() in n.lower()), None)
        if not name:
            raise RuntimeError("Chocolate não encontrado. Ligue o cabo USB-C no PC e confira se ele está ligado.")
        p = mido.open_output(name) if kind == "out" else mido.open_input(name)
        _ports[kind] = p
    return p


def _drain() -> list[mido.Message]:
    return list(_port("in").iter_pending())


def _send(msg: bytes) -> None:
    _port("out").send(mido.Message("sysex", data=choco.frame(msg)[1:-1]))


def _wait_reply(match, timeout: float = 1.5):
    t = time.time()
    while time.time() - t < timeout:
        for m in _port("in").iter_pending():
            if m.type == "sysex":
                r = choco.parse_reply(choco.unpack7(m.data))
                if r and match(r):
                    return r
        time.sleep(0.002)
    raise TimeoutError("O Chocolate não respondeu. Confira o cabo USB.")


def _read(addr: int, n: int) -> bytes:
    out = bytearray()
    with _lock:
        _drain()
        while len(out) < n:
            k = min(CHUNK, n - len(out))
            a = addr + len(out)
            _send(choco.read_msg(a, k))
            r = _wait_reply(lambda r: r["cmd"] == choco.CMD_READ and r["addr"] == a)
            if len(r["data"]) != k:
                raise RuntimeError(f"resposta curta no endereço {a}")
            out += r["data"]
    return bytes(out)


def _write(addr: int, data: bytes) -> None:
    with _lock:
        _drain()
        for off in range(0, len(data), CHUNK):
            _send(choco.write_msg(addr + off, data[off:off + CHUNK]))
            _wait_reply(lambda r: r["cmd"] == 0)


def _backup(tag: str) -> str:
    os.makedirs(BACKUPS, exist_ok=True)
    path = os.path.join(BACKUPS, time.strftime("%Y%m%d-%H%M%S") + f"-{tag}.fcp")
    with open(path, "wb") as f:
        f.write(_read(0, choco.SIZE))
    return path


def _group(group: int) -> int:
    if not 1 <= group <= 8:
        raise ValueError("grupo vai de 1 a 8")
    return group - 1


def _pedal(pedal: str) -> int:
    p = pedal.strip().upper()
    if p not in "ABCD" or len(p) != 1:
        raise ValueError("pedal é A, B, C ou D (E e F trocam de grupo e não têm mensagens próprias)")
    return "ABCD".index(p)


def _parse_msg(m) -> dict:
    """Aceita dict ou texto tipo "CC 54 127 ch5", "PC 3 ch1", "NoteOn 60 100"."""
    if isinstance(m, dict):
        return m
    words = str(m).replace(",", " ").split()
    out: dict = {"type": "CC", "channel": 1}
    nums = []
    for w in words:
        lw = w.lower()
        if lw in ("cc", "pc", "noteon", "noteoff"):
            out["type"] = {"cc": "CC", "pc": "PC", "noteon": "NoteOn", "noteoff": "NoteOff"}[lw]
        elif re.fullmatch(r"(ch|canal)\d+", lw):
            out["channel"] = int(re.sub(r"\D", "", lw))
        elif w.isdigit():
            nums.append(int(w))
        else:
            raise ValueError(f"não entendi '{w}' em '{m}'")
    if not nums:
        raise ValueError(f"falta o número em '{m}'")
    out["number"] = nums[0]
    out["value"] = nums[1] if len(nums) > 1 else 0
    return out


def _fmt(msgs: list[dict]) -> list[str]:
    return [f"{m['type']} {m['number']}" + ("" if m["type"] == "PC" else f" {m['value']}") + f" ch{m['channel']}"
            for m in msgs]


STOMP_SLOTS = {48 + i: f"{'AB'[i // 6]}{i % 6 + 1}" for i in range(12)}


def _stomp_view(h: bytes) -> dict:
    c = choco.parse_custom(h)
    return {"modo_do_aparelho": choco.DEVICE_MODES.get(c["mode"], f"código {c['mode']} (não mapeado)"),
            "canal": c["channel"],
            "pedais": {k: f"CC {v['cc']}" + (f" (slot {STOMP_SLOTS[v['cc']]} da Stomp)" if v["cc"] in STOMP_SLOTS else "")
                       + (", alterna liga/desliga" if v["toggle"] else ", 127 ao pisar e 0 ao soltar")
                       for k, v in c["pedals"].items()}}


@mcp.tool()
def read_stomp() -> dict:
    """Lê o modo do Chocolate e a configuração do modo Custom (modo stomp): canal e o CC de cada pedal A-D."""
    return _stomp_view(_read(0, 16))


@mcp.tool()
def set_stomp(pedals: dict, channel: int = 5, toggle: bool = True) -> dict:
    """GRAVA o modo Custom (modo stomp) no Chocolate e já deixa ele nesse modo, saindo do Advanced custom.
    Faz backup antes e confere depois.

    pedals: CC de cada pedal, ex. {"A": 48, "B": 49, "C": 50, "D": 56}; pedal que faltar fica como está.
    Na Ampero II Stomp, CC 48-53 = slots A1-A6 e CC 54-59 = slots B1-B6. Também aceita o slot: {"A": "A1"}.
    channel: canal MIDI (a Stomp da Julia escuta no 5). toggle=True: cada pisada alterna liga/desliga;
    False: liga ao pisar e desliga ao soltar. Vale na hora, sem religar."""
    slots = {v: k for k, v in STOMP_SLOTS.items()}
    want = {}
    for k, v in pedals.items():
        k = k.strip().upper()
        if k not in ("A", "B", "C", "D"):
            raise ValueError("pedal é A, B, C ou D")
        cc = slots[v.strip().upper()] if isinstance(v, str) and v.strip().upper() in slots else int(v)
        want[k] = (cc, toggle)
    before = _read(0, 16)
    new = choco.build_custom(before, int(channel), want)
    path = _backup("antes-stomp")
    _write(0, new)
    after = _read(0, 16)
    if after[:len(new)] != new:
        raise RuntimeError(f"A leitura de volta não bate com o que foi gravado. Backup em {path}")
    return {"gravado": _stomp_view(after), "antes": _stomp_view(before), "backup": path}


@mcp.tool()
def read_buttons(group: int | None = None) -> dict:
    """Lê o modo Advanced custom (formato do CubeSuite; vale no firmware 012, o 010 não lia).
    Sem group, mostra só os grupos com mensagem."""
    mem = _read(0, choco.ADDR_GROUP + 1)
    groups = {}
    for g in range(8) if group is None else [_group(group)]:
        peds = {}
        for p in range(4):
            r = choco.parse_record(mem[choco.rec_addr(g, p):])
            peds["ABCD"[p]] = {"modo": r["step_mode"], "banco_a": _fmt(r["bank_a"]), "banco_b": _fmt(r["bank_b"])}
        if group is not None or any(v["banco_a"] or v["banco_b"] for v in peds.values()):
            groups[str(g + 1)] = peds
    mode = mem[choco.ADDR_DEVICE_MODE]
    return {"modo_do_aparelho": choco.DEVICE_MODES.get(mode, f"código {mode} (não mapeado)"),
            "modos_de_pisada": choco.STEP_MODES, "grupos": groups}


@mcp.tool()
def set_button(group: int, pedal: str, step_mode: str, bank_a: list, bank_b: list | None = None) -> dict:
    """GRAVA um pedal do Chocolate (modo Advanced custom). Faz backup da memória inteira antes.

    group 1-8, pedal A-D. step_mode: "um banco", "alterna A/B", "pisa A / solta B", "pisada longa" ou
    "curta A / longa B". bank_a / bank_b: listas de mensagens em texto, ex. ["CC 48 127 ch5"] ou
    ["PC 3 ch5", "CC 60 127 ch5"], até 16 por banco. Para ligar/desligar um slot da Stomp: modo "alterna A/B",
    banco A com valor 127 e banco B com valor 0 (ou o contrário, conforme o slot comece ligado).
    Confirme com a usuária antes de gravar por cima de um pedal que já tem mensagens.
    Testado pisando no firmware 012, grupo 1; no 010 esse formato não funcionava."""
    g, p = _group(group), _pedal(pedal)
    if step_mode not in choco.STEP_MODES:
        raise ValueError(f"modo de pisada desconhecido; use um de {choco.STEP_MODES}")
    rec = (bytes([choco.STEP_MODES.index(step_mode)]) + choco.build_bank([_parse_msg(m) for m in bank_a])
           + choco.build_bank([_parse_msg(m) for m in (bank_b or [])]))
    addr = choco.rec_addr(g, p)
    before = choco.parse_record(_read(addr, len(rec)))
    path = _backup(f"antes-g{group}{pedal.upper()}")
    _write(addr, rec)
    after = _read(addr, len(rec))
    if after != rec:
        raise RuntimeError(f"A leitura de volta não bate com o que foi gravado. Backup em {path}")
    r = choco.parse_record(after)
    aviso = "Se o CubeSuite estiver aberto, feche e abra a janela do Chocolate para ver a mudança."
    mode = _read(choco.ADDR_DEVICE_MODE, 1)[0]
    if mode != choco.MODE_ADV1:
        aviso = (f"O aparelho está em {choco.DEVICE_MODES.get(mode, f'código {mode}')}, não no Advanced custom 1: "
                 "o pedal só usa isso quando o modo mudar. " + aviso)
    return {"gravado": f"grupo {group} pedal {pedal.upper()}", "modo": r["step_mode"],
            "banco_a": _fmt(r["bank_a"]), "banco_b": _fmt(r["bank_b"]),
            "antes": {"modo": before["step_mode"], "banco_a": _fmt(before["bank_a"]), "banco_b": _fmt(before["bank_b"])},
            "backup": path, "aviso": aviso}


@mcp.tool()
def listen(seconds: float = 10) -> list[str]:
    """Escuta o que o Chocolate manda pela USB enquanto a usuária pisa (até 60 s). Serve para conferir cada pedal.
    Só funciona com o Chocolate ligado no PC pelo cabo USB."""
    seconds = max(1.0, min(float(seconds), 60.0))
    got = []
    with _lock:
        _drain()
        t0 = time.time()
        while time.time() - t0 < seconds:
            for m in _port("in").iter_pending():
                if m.type != "sysex":
                    got.append(f"{time.time() - t0:5.1f}s  {m}")
            time.sleep(0.005)
    return got or ["nada recebido"]


@mcp.tool()
def backup() -> str:
    """Salva a memória inteira do Chocolate num arquivo .fcp (mesmo formato do Export do CubeSuite)."""
    return _backup("manual")


@mcp.tool()
def restore(path: str) -> dict:
    """Volta a configuração de um backup .fcp (ou Export do CubeSuite). Grava só os bytes que mudaram e faz
    um backup do estado atual antes. Confirme com a usuária antes de chamar."""
    with open(path, "rb") as f:
        want = f.read()
    if len(want) != choco.SIZE:
        raise ValueError(f"o arquivo tem {len(want)} bytes; um backup do Chocolate tem {choco.SIZE}")
    cur = _read(0, WORK_AREA)
    runs, i = [], 0
    while i < WORK_AREA:
        if cur[i] != want[i]:
            j = i
            while j < WORK_AREA and (cur[j] != want[j] or (j + 8 < WORK_AREA and cur[j:j + 8] != want[j:j + 8])):
                j += 1
            runs.append((i, j))
            i = j
        i += 1
    if not runs:
        return {"mudancas": 0}
    saved = _backup("antes-restore")
    for a, b in runs:
        _write(a, want[a:b])
    if _read(0, WORK_AREA) != want[:WORK_AREA]:
        raise RuntimeError(f"A leitura de volta não bate. Estado anterior salvo em {saved}")
    return {"trechos_gravados": len(runs), "bytes": sum(b - a for a, b in runs), "backup_anterior": saved}


if __name__ == "__main__":
    mcp.run()
