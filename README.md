# Periféricos RGB (Linux)

Aplicação desktop nativa em tema escuro (**PySide6**) para controlar a iluminação RGB, bateria e automação de periféricos em Linux sem depender de browsers WebHID nem de software Windows:

- **Keychron V6 Max (VIA v12):** controlo de efeitos, brilho, velocidade, cor, perfis rápidos, gravação na EEPROM e ligação automática via **2.4G (`Keychron Link`)** ou **USB-C**.
- **Glorious Model I 2 Wireless:** leitura passiva da percentagem de bateria, estado de carregamento e controlo de efeitos RGB, brilho, velocidade e cor via **2.4G** ou **USB-C**.
- **Automação Silenciosa (`systemd --user`):** cálculo solar offline (nascer/pôr do sol) e deteção de bloqueio de ecrã GNOME via D-Bus para ligar/desligar as luzes automaticamente.

---

## Requisitos

- Linux com **Python 3.10+** e **PySide6**
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

## Créditos

- Protocolo HID do Glorious Model I 2 Wireless baseado na documentação da comunidade: [`Krodity/glorious-i2`](https://github.com/Krodity/glorious-i2) e [`tvollstaedt/glorious-i2-battery`](https://github.com/tvollstaedt/glorious-i2-battery).
