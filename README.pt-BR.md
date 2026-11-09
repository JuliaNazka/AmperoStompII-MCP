# AmperoStompII-MCP

Dois servidores MCP para controlar, pelo Claude, uma pedaleira **Hotone Ampero II Stomp** e um controlador
**M-Vave Chocolate Plus**, ambos ligados no PC por USB MIDI.

*Read in English: [README.md](README.md)*

**App para celular:** [Ampero Remoto](https://github.com/JuliaNazka/ampero-remoto), o mesmo protocolo em JavaScript (PWA com Web MIDI) para controlar a pedaleira pelo Android. [Abrir o app](https://julianazka.github.io/ampero-remoto/).

Projeto pessoal, sem relação com a Hotone nem com a M-Vave. O protocolo SysEx dos dois aparelhos foi decifrado
observando o tráfego dos editores oficiais; use por sua conta.

## Por que este projeto existe

```mermaid
flowchart TD
    P1["Ampero II Stomp<br/>o manual só documenta CCs;<br/>cena, knob, modelo e salvar<br/>só pelo editor do Windows"]
    P2["Chocolate Plus<br/>configuração só pelo CubeSuite;<br/>o firmware 010 ignorava o<br/>modo avançado que ele gravava"]
    P3["Objetivo<br/>pedir mudanças em linguagem natural<br/>e tocar ao vivo sem PC"]
    D["Decifrar os protocolos SysEx<br/>observando os editores oficiais"]
    M["Dois servidores MCP<br/>o Claude lê e grava os dois aparelhos"]
    A["App Ampero Remoto<br/>o mesmo protocolo no celular Android"]
    L["Chocolate liga e desliga slots da Ampero<br/>por Bluetooth, sem PC no palco"]
    P1 --> D
    P2 --> D
    P3 --> D
    D --> M
    D --> A
    M --> L
```

## Arquitetura

```mermaid
flowchart TB
    C["Claude<br/>(Claude Code / Desktop)"]

    subgraph PC["PC"]
        direction LR
        subgraph AMP["ampero_mcp.py"]
            H["hotone.py<br/>quadros SysEx, nibbles, soma"]
            Z["lzo.py<br/>dump do patch em LZO1X"]
            J["models.json<br/>gerado pelo make_models.py"]
        end
        subgraph CHO["chocolate_mcp.py"]
            K["choco.py<br/>empacotamento 7 bits, mapa da memória"]
            B["backups .fcp"]
        end
        R["mido + python-rtmidi"]
        E["Editores oficiais<br/>editor Ampero II, CubeSuite<br/>(só referência do protocolo)"]
    end

    S["Hotone Ampero II Stomp"]
    F["M-Vave Chocolate Plus"]
    X["Receptor Bluetooth MS1"]
    W["Ampero Remoto<br/>Chrome no Android, Web MIDI"]

    C -- "MCP por stdio" --> AMP
    C -- "MCP por stdio" --> CHO
    AMP --> R
    CHO --> R
    E -. "banco de modelos do editor" .-> J
    R -- "USB MIDI: CC + SysEx" --> S
    R -- "USB MIDI: SysEx lê/grava memória" --> F
    F -- "MIDI Bluetooth: CC 48-59" --> X
    X -- "DIN MIDI IN" --> S
    W -- "USB OTG: mesmo SysEx" --> S
```

## ampero: Hotone Ampero II Stomp

Testado com firmware 2.4.0 e editor 1.4.0.

- **CC (tabela do manual 1.3.0):** `select_patch`, `step_patch`, `step_bank`, `set_slot` (liga e desliga A1–B6),
  `tuner`, `set_tempo`, `tap_tempo`, `set_patch_volume`, `expression`, `quick_access`, `looper`, `device_mode`,
  `send_raw`.
- **SysEx:** `read_patch` (cadeia, modelos, knobs e liga/desliga por cena), `list_patches`, `select_scene`,
  `set_param`, `set_model`, `save_patch`, `list_models`.
- `listen` mostra o que a pedaleira manda.

Mudanças de knob, modelo e cena valem na hora, mas só ficam no patch com `save_patch`.

Na Stomp: GLOBAL > MIDI > MIDI IN SOURCE em "USB Only" ou "Mixed". O canal de entrada USB tem que bater com
a variável `AMPERO_CHANNEL` (padrão 5).

**models.json** (nomes dos modelos e parâmetros) não está no repositório, porque os dados são da Hotone. Com o
editor oficial instalado, gere com:

```
python ampero/make_models.py
```

Detalhes do protocolo no topo de `ampero/hotone.py`. O dump de patch vem comprimido em LZO1X (`ampero/lzo.py`).

## chocolate: M-Vave Chocolate Plus

Lê e grava a configuração dos pedais pela USB, como o CubeSuite faz. A chave do Chocolate tem que estar em **U**.

- `read_buttons` / `set_button`: modo Advanced custom (modo de pisada e até 16 mensagens por banco).
  Funciona no firmware 012; o 010 não lê esse formato.
- `read_stomp` / `set_stomp`: modo Custom, um CC por pedal, alternando 127/0 a cada pisada.
- `listen`, `backup`, `restore`: escuta as pisadas e salva ou volta a memória inteira (arquivo .fcp, igual ao
  Export do CubeSuite).

Os backups ficam em `chocolate/backups/` (fora do repositório). Protocolo e mapa da memória no topo de
`chocolate/choco.py`.

## Instalação

Python 3.11 ou mais novo:

```
pip install mido python-rtmidi "mcp>=2"
```

Exemplo de registro no Claude Code (`~/.claude.json`, em `mcpServers`):

```json
"ampero": {
  "type": "stdio",
  "command": "python",
  "args": ["C:\\caminho\\para\\ampero\\ampero_mcp.py"],
  "env": { "AMPERO_CHANNEL": "5" }
},
"chocolate": {
  "type": "stdio",
  "command": "python",
  "args": ["C:\\caminho\\para\\chocolate\\chocolate_mcp.py"]
}
```

Com o Python da Microsoft Store, rode com `-E` antes do caminho do script.
