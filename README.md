# Periféricos RGB (Linux)

Aplicação desktop nativa em tema escuro (**PySide6**) para controlar a iluminação RGB, bateria e automação de periféricos em Linux sem depender de browsers WebHID nem de software Windows:

- **Keychron V6 Max (VIA v12):** controlo de efeitos, brilho, velocidade, cor, perfis rápidos, gravação na EEPROM e ligação automática via **2.4G (`Keychron Link`)** ou **USB-C**.
- **Glorious Model I 2 Wireless:** leitura passiva da percentagem de bateria, estado de carregamento e controlo de efeitos RGB, brilho, velocidade e cor via **2.4G** ou **USB-C**.
- **REDMI Headphones Neo (Bluetooth RFCOMM + PipeWire):** controlo de **ANC** (Desligado / Cancelamento de Ruído / Transparência), **Equalizador (EQ)**, **Perfis Rápidos**, **Ligação Dupla (Dual-Device)**, leitura/histórico de bateria, atalho na bandeja (*system tray*) e troca de codec/perfil **PipeWire** (`A2DP SBC / SBC-XQ` ↔ `HFP mSBC`).
- **Automação Silenciosa (`systemd --user`):** cálculo solar offline (nascer/pôr do sol) e deteção de bloqueio de ecrã GNOME via D-Bus para ligar/desligar as luzes automaticamente.

---

## Requisitos

- Linux com **Python 3.10+** (compilado com suporte `AF_BLUETOOTH` / `BTPROTO_RFCOMM`) e **PySide6**
- **BlueZ** (`bluetoothctl`) e **PipeWire / PulseAudio** (`pactl`) para os **REDMI Headphones Neo**
- Regras `udev` com permissão de leitura/escrita em `/dev/hidraw*` para:
  - **Keychron** (`idVendor == "3434"`)
  - **Glorious Model I 2** (`idVendor == "093a"`)

Exemplo de regra `/etc/udev/rules.d/99-perifericos-rgb.rules`:

```udev
KERNEL=="hidraw*", SUBSYSTEM=="hidraw", ATTRS{idVendor}=="3434", MODE="0660", TAG+="uaccess"
KERNEL=="hidraw*", SUBSYSTEM=="hidraw", ATTRS{idVendor}=="093a", MODE="0660", TAG+="uaccess"
```

Recarregar regras:

```bash
sudo udevadm control --reload-rules && sudo udevadm trigger
```

---

## Instalação rápida

```bash
chmod +x install.sh
./install.sh
```

O script instala as dependências, configura as regras `udev`, cria o atalho no menu de aplicações e ativa o serviço silencioso `systemd --user`.

---

## Como usar manual / terminal

```bash
python3 app.py
```

---

## Nota sobre VIA por 2.4G no Keychron V6 Max (`2025q3`)

Na branch `2025q3` de `Keychron/qmk_firmware`, o ficheiro `keyboards/keychron/common/wireless/wireless.c` tinha dois impedimentos no handler `EVT_RAW_HID`:

1. O bloco estava protegido por `#if defined(RAW_ENABLE) && defined(WILRESS_RAW_ENABLE)` (typo não definido).
2. A chamada interna usava `via_raw_hid_receive(...)` em vez de `raw_hid_receive(...)`.

Ao corrigir para:

```c
#if defined(RAW_ENABLE)
            case EVT_RAW_HID:
#    ifdef VIA_ENABLE
                raw_hid_receive(RAW_HID_SRC_WIRELESS, event.params.raw_hid_data, 32);
#    else
                kc_raw_hid_rx(RAW_HID_SRC_WIRELESS, event.params.raw_hid_data, 32);
#    endif
                lpm_timer_reset();
                break;
#endif
```

e com o dongle **Keychron Link (`3434:d030`)** na versão **`d.3.0`** + módulo wireless **`0.2.4`**, o protocolo VIA passa a responder diretamente em modo 2.4 GHz.

---

## Estrutura do Projeto

- `app.py` — Janela principal (`PySide6`), separador do teclado, bandeja do sistema (*system tray*) e automação.
- `via_hid.py` — Protocolo VIA/QMK por HID para o **Keychron V6 Max**.
- `glorious_hid.py` & `mouse_tab.py` — Protocolo HID e separador do **Glorious Model I 2 Wireless**.
- `redmi_bt.py` & `headphones_tab.py` — Cliente RFCOMM, integração `pactl` e separador dos **REDMI Headphones Neo**.
- `battery_log.py` & `battery_dialog.py` — Histórico de bateria persistente e gráfico de autonomia.
- `automation.py` — Cálculo solar, monitorização GNOME D-Bus e notificações de bateria.

---

## Protocolo Bluetooth dos REDMI Headphones Neo

Os **REDMI Headphones Neo** utilizam **Bluetooth Classic RFCOMM** no **Canal 6** (UUID `0000fd2d-0000-1000-8000-00805f9b34fb`) com enquadramento binário `FE DC BA <flag> <opcode> <len:2B> <payload> EF` (protocolo Xiaomi/JieLi RCSP):

- **`0x02`** — Informação do dispositivo e percentagem de bateria (com fallback passivo para `bluetoothctl info`).
- **`0x08`** — Modo de controlo de ruído (`0x00` Desligado, `0x01` Cancelamento de Ruído, `0x02` Transparência).
- **`0xF2` / `0xF3`** — Leitura e escrita de atributos:
  - `0x0007`: Equalizador (`0x15` Som Balanceado, `0x05` Melhorar Graves, `0x06` Melhorar Agudos, `0x01` Melhorar Voz, `0x0A` Personalizado).
  - `0x000B`: Estado atual de ANC.
  - `0x0004`: Ligação dupla (*Dual-Device*).

> **Nota (*Dual-Device*):** o canal RFCOMM 6 aceita apenas uma ligação de controlo de cada vez. Se a app *Xiaomi Earbuds* no telemóvel estiver ativa a segurar o canal, a app no Linux mostra o estado e continua a ler a bateria via BlueZ.

---

## Créditos

- Protocolo HID do Glorious Model I 2 Wireless baseado na documentação da comunidade: [`Krodity/glorious-i2`](https://github.com/Krodity/glorious-i2) e [`tvollstaedt/glorious-i2-battery`](https://github.com/tvollstaedt/glorious-i2-battery).
