"""MCP para controlar a pedaleira Hotone Ampero II Stomp via USB MIDI.

Usa a tabela "MIDI Control Information List" do manual oficial (firmware 1.3.0).
Na Stomp: GLOBAL > MIDI > MIDI IN SOURCE precisa estar em "USB Only" ou "Mixed",
e AMPERO_CHANNEL (padrão 5) tem que bater com "Input Channel (USB)".
O editor Ampero II pode ficar aberto ao mesmo tempo.
"""
from __future__ import annotations

import json
import os
import re
import struct
import sys
import threading
import time
import unicodedata

import mido
from mcp.server import MCPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hotone  # noqa: E402
import lzo  # noqa: E402

mcp = MCPServer("ampero", instructions=(
    "Controla a Hotone Ampero II Stomp por USB MIDI. Patches no formato A00-1 a A99-3; slots A1-A6 (linha de "
    "cima) e B1-B6 (linha de baixo). Use read_patch antes de mexer em knob ou modelo. Mudanças de knob, modelo "
    "e cena valem na hora mas NÃO são salvas no patch: trocar de patch descarta tudo."))

PORT_HINT = "Ampero"
CHANNEL = int(os.environ.get("AMPERO_CHANNEL", "5")) - 1  # Input Channel (USB) da Stomp
_lock = threading.Lock()
_out: mido.ports.BaseOutput | None = None

SLOTS = ["A1", "A2", "A3", "A4", "A5", "A6", "B1", "B2", "B3", "B4", "B5", "B6"]
SLOT_CC = {name: 48 + i for i, name in enumerate(SLOTS)}

LOOPER_CC = {
    "menu_on": (62, 127), "menu_off": (62, 0),
    "rec_overdub": (63, 127),
    "play": (64, 127), "stop": (64, 0),
    "half_speed": (65, 0), "normal_speed": (65, 127),
    "reverse": (66, 0), "forward": (66, 127),
    "undo_redo": (67, 127),
    "clear": (68, 127),
    "pre": (71, 127), "post": (71, 0),
    "chain_a": (72, 0), "chain_b": (72, 127),
}


def _find_port(names: list[str]) -> str | None:
    return next((n for n in names if PORT_HINT.lower() in n.lower()), None)


def _output() -> mido.ports.BaseOutput:
    global _out
    if _out is None or _out.closed:
        name = _find_port(mido.get_output_names())
        if not name:
            raise RuntimeError("Ampero II não encontrada. Confira o cabo USB e se a pedaleira está ligada.")
        _out = mido.open_output(name)
    return _out


def _send(*msgs: mido.Message) -> None:
    global _out
    with _lock:
        try:
            port = _output()
            for m in msgs:
                port.send(m)
        except Exception:
            # porta pode ter caído (USB reconectado): reabre uma vez
            if _out is not None:
                _out.close()
            _out = None
            port = _output()
            for m in msgs:
                port.send(m)


def _cc(cc: int, value: int, channel: int = CHANNEL) -> mido.Message:
    return mido.Message("control_change", control=cc, value=max(0, min(127, value)), channel=channel)


def _onoff(on: bool) -> int:
    return 127 if on else 0


def _patch_index(patch: str | int) -> int:
    """'A05-1', '05-1', 'P05-1' ou índice 0-299 -> índice 0-299."""
    if isinstance(patch, int) or str(patch).strip().isdigit():
        idx = int(patch)
    else:
        m = re.fullmatch(r"\s*[A-Za-z]?\s*(\d{1,2})\s*-\s*([123])\s*", str(patch))
        if not m:
            raise ValueError(f"Patch inválido: {patch!r}. Use 'A05-1' (banco 00-99, patch 1-3) ou índice 0-299.")
        idx = int(m.group(1)) * 3 + int(m.group(2)) - 1
    if not 0 <= idx <= 299:
        raise ValueError("Patch fora da faixa: são 300 patches (A00-1 a A99-3).")
    return idx


def _patch_label(idx: int) -> str:
    return f"A{idx // 3:02d}-{idx % 3 + 1}"


@mcp.tool()
def status() -> dict:
    """Mostra as portas MIDI e se a Ampero II está conectada."""
    outs, ins = mido.get_output_names(), mido.get_input_names()
    return {
        "conectada": bool(_find_port(outs)),
        "porta_saida": _find_port(outs),
        "porta_entrada": _find_port(ins),
        "todas_saidas": outs,
        "todas_entradas": ins,
        "canal_midi": CHANNEL + 1,
        "lembrete": "Na Stomp: MIDI IN SOURCE em USB Only ou Mixed, e Input Channel (USB) igual a canal_midi (ou Omni).",
    }


@mcp.tool()
def select_patch(patch: str) -> str:
    """Troca para um patch. Aceita 'A05-1' (banco 00-99, patch 1-3) ou índice 0-299."""
    idx = _patch_index(patch)
    _send(_cc(0, idx // 128), mido.Message("program_change", program=idx % 128, channel=CHANNEL))
    return f"Patch {_patch_label(idx)} enviado (CC0={idx // 128}, PC={idx % 128})."


@mcp.tool()
def step_patch(direction: str) -> str:
    """Patch anterior ou seguinte. direction: 'next' ou 'prev'."""
    cc = {"next": 27, "prev": 26}[direction]
    _send(_cc(cc, 127))
    return f"Patch {direction} enviado."


@mcp.tool()
def step_bank(direction: str, wait_mode: bool = False) -> str:
    """Banco anterior ou seguinte. direction: 'next' ou 'prev'. wait_mode: só troca o banco sem carregar o patch."""
    cc = {("prev", False): 22, ("next", False): 23, ("prev", True): 24, ("next", True): 25}[(direction, wait_mode)]
    _send(_cc(cc, 127))
    return f"Banco {direction} enviado{' (modo espera)' if wait_mode else ''}."


@mcp.tool()
def set_slot(slot: str, on: bool) -> str:
    """Liga/desliga um slot da cadeia de efeitos. slot: A1-A6 (linha de cima) ou B1-B6 (linha de baixo)."""
    key = slot.strip().upper()
    if key not in SLOT_CC:
        raise ValueError(f"Slot inválido: {slot}. Use {', '.join(SLOTS)}.")
    _send(_cc(SLOT_CC[key], _onoff(on)))
    return f"Slot {key} {'ligado' if on else 'desligado'}."


@mcp.tool()
def tuner(on: bool) -> str:
    """Liga/desliga o afinador."""
    _send(_cc(60, _onoff(on)))
    return f"Afinador {'ligado' if on else 'desligado'}."


@mcp.tool()
def set_tempo(bpm: int) -> str:
    """Define o BPM global (40-300)."""
    if not 40 <= bpm <= 300:
        raise ValueError("BPM entre 40 e 300.")
    msb, lsb = (0, bpm) if bpm <= 127 else (1, bpm - 128) if bpm <= 255 else (2, bpm - 256)
    _send(_cc(74, msb), _cc(75, lsb))
    return f"Tempo {bpm} BPM enviado."


@mcp.tool()
def tap_tempo(bpm: int | None = None, taps: int = 4) -> str:
    """Dá tap tempo. Sem bpm: um toque. Com bpm: dá 'taps' toques no intervalo desse BPM."""
    if bpm is None:
        _send(_cc(76, 127))
        return "Tap enviado."
    interval = 60.0 / bpm
    for i in range(taps):
        _send(_cc(76, 127))
        if i < taps - 1:
            time.sleep(interval)
    return f"{taps} taps a {bpm} BPM."


@mcp.tool()
def set_patch_volume(volume: int) -> str:
    """Volume do patch atual (0-100)."""
    _send(_cc(7, max(0, min(100, volume))))
    return f"Volume do patch {volume}."


@mcp.tool()
def expression(value: int | None = None, on: bool | None = None) -> str:
    """Pedal de expressão: value 0-127 move o pedal; on liga/desliga o EXP."""
    msgs, done = [], []
    if on is not None:
        msgs.append(_cc(13, _onoff(on)))
        done.append(f"EXP {'on' if on else 'off'}")
    if value is not None:
        msgs.append(_cc(11, value))
        done.append(f"posição {value}")
    if not msgs:
        raise ValueError("Informe value e/ou on.")
    _send(*msgs)
    return "Expressão: " + ", ".join(done) + "."


@mcp.tool()
def quick_access(param: int, value: int | None = None, nudge: str | None = None) -> str:
    """Parâmetros de acesso rápido 1-3 do patch. value 0-127 define direto; nudge 'up'/'down' anda um passo."""
    if param not in (1, 2, 3):
        raise ValueError("param deve ser 1, 2 ou 3.")
    base = 16 + (param - 1) * 2
    if value is not None:
        _send(_cc(base, value))
        return f"Quick Access {param} = {value}."
    if nudge in ("up", "down"):
        _send(_cc(base + 1, 127 if nudge == "up" else 0))
        return f"Quick Access {param} um passo {'acima' if nudge == 'up' else 'abaixo'}."
    raise ValueError("Informe value ou nudge ('up'/'down').")


@mcp.tool()
def looper(action: str, level: int | None = None) -> str:
    """Looper. action: menu_on, menu_off, rec_overdub, play, stop, half_speed, normal_speed,
    reverse, forward, undo_redo, clear, pre, post, chain_a, chain_b, rec_level, loop_level (com level 0-100)."""
    if action in ("rec_level", "loop_level"):
        if level is None:
            raise ValueError("Informe level 0-100.")
        _send(_cc(69 if action == "rec_level" else 70, max(0, min(100, level))))
        return f"Looper {action} = {level}."
    if action not in LOOPER_CC:
        raise ValueError(f"Ação inválida. Use: {', '.join(list(LOOPER_CC) + ['rec_level', 'loop_level'])}.")
    _send(_cc(*LOOPER_CC[action]))
    return f"Looper: {action}."


@mcp.tool()
def device_mode(unit_mode: str | None = None, display_mode: int | None = None,
                ctrl: str | None = None, screen_locked: bool | None = None) -> str:
    """Modos do aparelho. unit_mode: 'stomp'/'patch'; display_mode: 1/2; ctrl: 'A' (LED vermelho)/'B' (verde);
    screen_locked: trava/destrava a tela."""
    msgs, done = [], []
    if unit_mode:
        msgs.append(_cc(28, 127 if unit_mode == "patch" else 0)); done.append(f"modo {unit_mode}")
    if display_mode:
        msgs.append(_cc(29, 127 if display_mode == 2 else 0)); done.append(f"tela modo {display_mode}")
    if ctrl:
        msgs.append(_cc(73, 127 if ctrl.upper() == "B" else 0)); done.append(f"CTRL {ctrl.upper()}")
    if screen_locked is not None:
        msgs.append(_cc(77, 0 if screen_locked else 127)); done.append("tela travada" if screen_locked else "tela destravada")
    if not msgs:
        raise ValueError("Nada para mudar.")
    _send(*msgs)
    return ", ".join(done) + "."


@mcp.tool()
def send_raw(cc: int | None = None, value: int | None = None, program: int | None = None,
             channel: int | None = None) -> str:
    """MIDI cru, para comandos fora da tabela (ex.: cenas no firmware 2.x). cc+value ou program.
    channel 1-16; sem channel usa o canal configurado."""
    ch = CHANNEL if channel is None else channel - 1
    channel = ch + 1
    if program is not None:
        _send(mido.Message("program_change", program=program, channel=ch))
        return f"PC {program} (canal {channel})."
    if cc is None or value is None:
        raise ValueError("Informe cc e value, ou program.")
    _send(_cc(cc, value, ch))
    return f"CC{cc}={value} (canal {channel})."


# ---------- SysEx (protocolo do editor, ver hotone.py) ----------

POSITIONS = SLOTS  # A1..A6 = posições 0-5 na tela, B1..B6 = 6-11
_db = json.load(open(os.path.join(os.path.dirname(__file__), "models.json"), encoding="utf-8"))
MODULE_NAME = {m["index"]: m["name"] for m in _db["modules"]}
MODEL_BY_CODE = {m["code"]: m for m in _db["models"]}


def _send_sysex(msg: bytes, op: int = 0x12) -> None:
    _send(*[mido.Message("sysex", data=f[1:-1]) for f in hotone.frames(msg, op)])


def _request(cmd: int, payload: bytes | None = None, timeout: float = 4.0) -> bytes:
    """Pedido de leitura (op 0x11); devolve a carga da resposta com o mesmo cmd."""
    name = _find_port(mido.get_input_names())
    if not name:
        raise RuntimeError("Porta de entrada da Ampero II não encontrada.")
    asm = hotone.Assembler()
    with mido.open_input(name) as inp:
        _send_sysex(hotone.inner(cmd, payload), op=0x11)
        end = time.time() + timeout
        while time.time() < end:
            for m in inp.iter_pending():
                if m.type != "sysex":
                    continue
                got = asm.feed(list(m.data))
                if got:
                    c, body = hotone.split_inner(got[1])
                    if c == cmd:
                        return body
            time.sleep(0.003)
    raise TimeoutError(f"A pedaleira não respondeu ao pedido {cmd:08x}.")


def _wait_quiet(quiet: float = 0.35, max_wait: float = 3.0) -> None:
    """Espera a pedaleira parar de transmitir (dumps para nós e para o editor) antes de escrever.
    Escrever no meio de um dump travou o USB MIDI da Stomp uma vez."""
    name = _find_port(mido.get_input_names())
    if not name:
        return
    with mido.open_input(name) as inp:
        start = last = time.time()
        while time.time() - last < quiet and time.time() - start < max_wait:
            if any(True for _ in inp.iter_pending()):
                last = time.time()
            time.sleep(0.01)


def _u32_reply(body: bytes) -> int:
    return struct.unpack_from("<I", body, 1)[0]


def _current_patch() -> tuple[int, dict]:
    idx = _u32_reply(_request(0x00090000))
    body = _request(hotone.CMD_PATCH, struct.pack("<I", idx))
    return idx, hotone.parse_patch(hotone.decode_patch_dump(body))


def _current_scene() -> int:
    return _u32_reply(_request(hotone.CMD_SCENE))


def _slot_info(p: dict, pos: int, scene: int) -> dict:
    sid = p["order"][pos]
    mod, code = p["modules"][sid], p["codes"][sid]
    info = {"slot": POSITIONS[pos], "slot_id": sid}
    if mod == 0xFF:
        return {**info, "module": None, "model": None}
    model = MODEL_BY_CODE.get(code)
    info.update(module=MODULE_NAME.get(mod, mod), model=model["name"] if model else f"código {code:#x}",
                on=bool(p["onoff"][scene][sid]))
    if model:
        vals = p["params"][scene][sid]
        info["params"] = {w["name"]: round(vals[w["id"]], 2) for w in model["params"]}
    return info


def _resolve_slot(slot: str) -> int:
    key = slot.strip().upper()
    if key not in POSITIONS:
        raise ValueError(f"Slot inválido: {slot}. Use {', '.join(POSITIONS)}.")
    return POSITIONS.index(key)


def _find_model(name: str, module: str | None = None) -> dict:
    n = name.strip().lower()
    pool = [m for m in _db["models"] if module is None or MODULE_NAME[m["module"]].lower() == module.lower()]
    exact = [m for m in pool if m["name"].lower() == n]
    if exact:
        return exact[0]
    part = [m for m in pool if n in m["name"].lower()]
    if len(part) == 1:
        return part[0]
    if not part:
        raise ValueError(f"Modelo '{name}' não encontrado. Use list_models para ver os nomes.")
    raise ValueError("Mais de um modelo bate: " + ", ".join(f"{m['name']} ({MODULE_NAME[m['module']]})" for m in part[:15]))


@mcp.tool()
def read_patch() -> dict:
    """Lê o patch atual da pedaleira: nome, cena atual, e cada slot (A1-B6) com módulo, modelo,
    liga/desliga e valores dos knobs na cena atual (ao vivo, incluindo edições ainda não salvas)."""
    idx, p = _current_patch()
    scene = _current_scene()
    return {
        "patch": _patch_label(idx), "name": p["name"], "style": p["style"],
        "scene": scene + 1, "scene_names": p["scene_names"],
        "slots": [_slot_info(p, pos, scene) for pos in range(12)],
    }


@mcp.tool()
def list_patches(search: str | None = None) -> list[str]:
    """Lista os 300 patches (A00-1 a A99-3) com nome. search filtra pelo nome."""
    plain = lzo.lzo1x_decompress(_request(0x01080000))
    names = [plain[600 + 17 * i:617 + 17 * i].split(b"\0")[0].decode("utf-8", "replace") for i in range(300)]
    rows = [f"{_patch_label(i)} {n}" for i, n in enumerate(names)]
    if search:
        rows = [r for r in rows if search.lower() in r.lower()]
    return rows


@mcp.tool()
def select_scene(scene: int) -> str:
    """Troca a cena do patch atual (1, 2 ou 3)."""
    if scene not in (1, 2, 3):
        raise ValueError("Cena 1, 2 ou 3.")
    _wait_quiet()
    _send_sysex(hotone.scene_msg(scene - 1))
    return f"Cena {scene} enviada."


@mcp.tool()
def set_param(slot: str, param: str, value: float) -> str:
    """Gira um knob de um slot. slot: A1-B6; param: nome do knob (ex.: 'Gain', 'Bass', 'Mix');
    value: na escala do editor (normalmente 0-100; listas e chaves usam o número da opção, a partir de 0).
    A mudança vale na hora, mas não é salva no patch."""
    pos = _resolve_slot(slot)
    _, p = _current_patch()
    sid = p["order"][pos]
    model = MODEL_BY_CODE.get(p["codes"][sid])
    if not model:
        raise ValueError(f"Slot {slot.upper()} está vazio ou com modelo desconhecido.")
    w = next((w for w in model["params"] if w["name"].lower() == param.strip().lower()), None)
    if not w:
        raise ValueError(f"{model['name']} não tem '{param}'. Knobs: {', '.join(x['name'] for x in model['params'])}.")
    if not w["min"] <= value <= w["max"]:
        raise ValueError(f"{w['name']} vai de {w['min']:g} a {w['max']:g}.")
    _wait_quiet()
    _send_sysex(hotone.param_msg(sid, w["id"], float(value)))
    return f"{slot.upper()} {model['name']}: {w['name']} = {value:g} (não salvo)."


@mcp.tool()
def set_model(slot: str, model: str, module: str | None = None) -> str:
    """Troca o modelo de um slot (ex.: set_model('A4', 'Voxy 30HW Norm')). module (DYN, AMP, DLY...)
    ajuda quando o nome existe em mais de um módulo. O slot liga e os knobs voltam ao padrão do modelo.
    Não salva o patch."""
    pos = _resolve_slot(slot)
    m = _find_model(model, module)
    _, p = _current_patch()
    sid = p["order"][pos]
    _wait_quiet()
    _send_sysex(hotone.model_msg(sid, m["module"], m["code"]))
    _wait_quiet()
    return f"{slot.upper()} agora é {m['name']} ({MODULE_NAME[m['module']]}), ligado, knobs no padrão (não salvo)."


@mcp.tool()
def save_patch(slot: str | None = None, name: str | None = None, overwrite: bool = False) -> str:
    """GRAVA o patch em edição na memória da pedaleira. Só use quando a usuária pedir para salvar.
    slot: A00-1..A99-3; vazio = o patch atual (grava por cima dele). name: até 16 letras sem acento
    (vazio = mantém o nome). Para gravar em OUTRO slot que não esteja "Empty", passe overwrite=True
    só depois que ela confirmar que aquele patch pode ser substituído. Depois de salvar em outro slot,
    a pedaleira passa a usar esse slot (como o editor faz)."""
    idx, p = _current_patch()
    target = idx if slot is None else _patch_index(slot)
    plain = lzo.lzo1x_decompress(_request(0x01080000))
    old = plain[600 + 17 * target:617 + 17 * target].split(b"\0")[0].decode("utf-8", "replace")
    if target != idx and old != "Empty" and not overwrite:
        raise ValueError(f"{_patch_label(target)} tem o patch '{old}'. Confirme com a usuária e chame de novo com overwrite=True.")
    clean = unicodedata.normalize("NFD", name or p["name"]).encode("ascii", "ignore").decode().strip()[:16] or p["name"]
    _wait_quiet()
    _send_sysex(hotone.save_msg(target, clean))
    time.sleep(0.5)
    _wait_quiet()
    if target != idx:
        _send_sysex(hotone.select_msg(target))
    where = "por cima do atual" if target == idx else f"(antes: '{old}')"
    return f"Salvo em {_patch_label(target)} '{clean}' {where}."


@mcp.tool()
def list_models(module: str | None = None, search: str | None = None) -> list[str]:
    """Lista modelos de efeito. module: DYN, FREQ, WAH, DRV, AMP, PRE AMP, CAB, IR, EQ, MOD, DLY, RVB, ...;
    search filtra pelo nome. Mostra os knobs de cada um."""
    rows = []
    for m in _db["models"]:
        if module and MODULE_NAME[m["module"]].lower() != module.lower():
            continue
        if search and search.lower() not in m["name"].lower():
            continue
        knobs = ", ".join(w["name"] for w in m["params"])
        rows.append(f"{MODULE_NAME[m['module']]} | {m['name']} | {knobs}")
    return rows[:200]


@mcp.tool()
def listen(seconds: float = 5.0) -> list[dict]:
    """Escuta o que a pedaleira manda pela USB por alguns segundos (CC, PC, SysEx)."""
    name = _find_port(mido.get_input_names())
    if not name:
        raise RuntimeError("Porta de entrada da Ampero II não encontrada.")
    got = []
    with mido.open_input(name) as inp:
        end = time.time() + min(seconds, 60)
        while time.time() < end:
            for m in inp.iter_pending():
                if m.type == "sysex":
                    got.append({"type": "sysex", "len": len(m.data), "head": list(m.data[:10])})
                else:
                    got.append({"type": m.type, **{k: v for k, v in m.dict().items() if k not in ("type", "time")}})
            time.sleep(0.005)
    return got


if __name__ == "__main__":
    mcp.run()
