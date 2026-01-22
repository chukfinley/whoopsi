# Whoop 5.0 (Maverick) Firmware — Was steckt drin?

*Analyse der Firmware v50.35.2.0 (Build vom 04.11.2025) | Ambiq Apollo4 Blue Plus SoC*

---

## Auf einen Blick

| Eigenschaft | Wert |
|---|---|
| **Chip** | Ambiq Apollo4 Blue Plus (ARM Cortex-M4F, 96 MHz, BLE integriert) |
| **Firmware-Groesse** | 1.548.504 Bytes (1,5 MB ARM-Binary) |
| **Sprache** | C (komplett, kein C++) |
| **Betriebssystem** | QP RTOS (Quantum Platform) — Event-driven Active Object Framework |
| **Funktionen im Binary** | ~13.000 (3.210 via radare2, 9.832 weitere via angr) |
| **Strings im Binary** | 20.075 (BLE: 856, Sensor: 457, Debug: 439, FW-Update: 295) |
| **Sensoren** | PPG/AFE, IMU (6-Achsen), Temperatur, Fuel Gauge, LED, Haptic, ECG, NFC/RFID |
| **Sicherheit** | CRC32-only (keine kryptographischen Signaturen gefunden) |

---

## 1. Der Chip: Ambiq Apollo4 Blue Plus

Das Whoop 5.0 (intern "Maverick" genannt) basiert auf dem **Ambiq Apollo4 Blue Plus** — einem Ultra-Low-Power ARM Cortex-M4F SoC. Ambiq ist auf Wearables spezialisiert und optimiert extrem auf Stromverbrauch.

**Speicher-Layout:**

```
0x00018000 - 0x001FFFFF   MRAM (Flash-Ersatz, 1,9 MB)  ← Firmware lebt hier
0x10000000 - 0x101FFFFF   SRAM (2 MB)                   ← RAM fuer Laufzeit
0x40000000+                Peripherie-Register            ← Hardware-Steuerung
```

MRAM (Magneto-Resistive RAM) ist Ambiqs Flash-Alternative — schneller, weniger Strom, aber gleiche Funktion: nicht-fluechtig, ueberlebt Reboot. Die Firmware wird beim Update hierher kopiert.

**Der Chip hat BLE eingebaut** — das ist nicht wie bei aelteren Whoops (Gen4) mit einem separaten Nordic-Chip. Stattdessen hat der Apollo4 einen integrierten **"Cooper" BLE Controller** mit eigenem Cortex-M0 Kern. Das Haupt-Firmware spricht ueber ein internes SBL-Interface mit Cooper.

---

## 2. Das Betriebssystem: QP RTOS

Whoop benutzt **kein** FreeRTOS, Zephyr oder ein anderes Standard-RTOS. Stattdessen laeuft **QP (Quantum Platform)** — ein Event-driven Active Object Framework.

### Was heisst das?

Statt klassischer Threads/Tasks hat QP sogenannte **Active Objects (AOs)**. Jedes AO ist eine State Machine, die Events empfaengt und verarbeitet. Die AOs kommunizieren ausschliesslich ueber **Signals** (Events) — kein shared memory, keine Locks, keine Race Conditions.

### Die 24 Active Objects

Die Firmware hat **24 AOs** (19 Production + 5 Test/Integration):

| Active Object | Source File | Was es tut |
|---|---|---|
| **Supervisor** | `./src/supervisor_ao.c` | System-Manager, Fehlerbehandlung, orchestriert alles |
| **Sensors** | `./modules/sensors/src/sensors_ao.c` | PPG/SpO2/HR Datenerfassung |
| **Analytics** | `./modules/analytics/src/analytics_ao.c` | Signal Processing (HR, HRV, SpO2 Berechnung) |
| **BLE_Command** | `./modules/ble/src/ble_cmd_ao.c` | AA01-Protokoll, alle BLE-Kommandos |
| **Whoop_Cordio** | `./src/whoop_cordio/whoop_cordio_ao.c` | Cordio BLE Stack Integration |
| **Flash** | *(in Supervisor/Sensors)* | Circular Buffer, Trim, Datenspeicherung |
| **I2C** | `./src/i2c_ao.c` | I2C-Bus-Treiber (Multi-Bus, alle Sensoren haengen daran) |
| **Fuel_Gauge** | `./modules/fuel_gauge/.../onsemi_fuel_gauge_ao.c` | Batterie-Management Logik |
| **LC709205F** | `./modules/fuel_gauge/.../lc709205f_ao.c` | LC709205F Chip-Treiber (I2C) |
| **Listener** | `./modules/wpt/src/listener_ao.c` | WPT/Wireless Power Transfer (Laden) |
| **UI_Manager** | `./modules/ui_subsystem/src/ui_manager_ao.c` | Orchestriert LED + Haptics |
| **LED_UI** | `./modules/ui_subsystem/src/led_ui_ao.c` | LED-Muster Logik |
| **LP5562** | `./modules/ui_subsystem/src/lp5562_ao.c` | LP5562 RGB LED Chip-Treiber |
| **Haptics** | `./modules/ui_subsystem/src/haptics_ao.c` | Vibrations-Muster Logik |
| **DRV2625** | `./modules/ui_subsystem/src/drv2625_ao.c` | DRV2625 Haptic Chip-Treiber |
| **Temp_Sensors** | `./src/temp_sensors_ao.c` | Temperatur-Management |
| **AS6221** | `./src/as6221_ao.c` | AS6221 Temperatur Chip-Treiber |
| **Tag_Reader** | `./modules/tag_reader/src/tag_reader_ao.c` | RFID/NFC (Zubehoer-Erkennung) |
| **ECG_Control** | `./modules/ecg_control/src/ecg_control_ao.c` | EKG-Aufnahme |
| **Debug_Menu** | `./modules/debug_menu/src/debugmenu_ao.c` | UART Debug-Konsole |
| **ITEST** | `./src/itest_ao.c` | Fertigungs-Tests |
| *3x ITEST_** | `./test/integration_test/itest_*_ao.c` | Integration Tests |

### Signal-System

Die AOs tauschen insgesamt **394 Signals** aus. Die aktivsten Subsysteme:

```
LISTENER (Laden):     67 Signals  ████████████████████████████████████
FUEL (Batterie):      48 Signals  █████████████████████████
BLE:                  44 Signals  ███████████████████████
SENSORS:              27 Signals  ██████████████
FLASH:                25 Signals  █████████████
DRV2625 (Haptic):     18 Signals  █████████
HAPTICS:              18 Signals  █████████
LC709205F:            18 Signals  █████████
AS6221 (Temp):        15 Signals  ████████
UI:                   15 Signals  ████████
I2C:                  12 Signals  ██████
LP5562 (LED):         12 Signals  ██████
SUPERVISOR:           10 Signals  █████
ANALYTICS:             7 Signals  ███
ECG:                   3 Signals  █
```

**Spannend**: Der Listener (Lade-Subsystem) hat die meisten Signals und den komplexesten State Machine (26 States). Das Laden ist offenbar der komplizierteste Teil der Firmware.

### Wie die Kommunikation laeuft

```
Supervisor (Chef)
    ↓ Befehle                    ↑ Reports (READY/ERROR)
    ↓                            ↑
┌───┼────────────────────────────┼───┐
│ Sensors AO  →  Analytics AO  →  Flash AO │  Daten-Pipeline
│   (PPG lesen)   (HR berechnen)  (speichern)│
└────────────────────────────────────────────┘
         ↕ I2C Signals
     I2C AO (Bus-Treiber)
         ↕
    Hardware (Sensoren)
```

Jedes Kind-AO meldet sich beim Supervisor mit `*_READY_REPORT_SIG` (bereit) oder `*_ERROR_REPORT_SIG` (Fehler). Der Supervisor entscheidet dann, was passiert.

---

## 3. Die Hardware-Sensoren

Alle Sensoren haengen am **I2C-Bus** und werden vom I2C AO angesteuert:

| Sensor | Chip | I2C-Adresse | Aufgabe |
|---|---|---|---|
| **IMU** | TDK ICM-45686 | 0x68 / 0x69 | 6-Achsen Beschleunigung + Gyroskop (Bewegungserkennung) |
| **RGB LED** | TI LP5562 | 0x30 | Status-LEDs (Farben, Muster, Helligkeit) |
| **Haptic Motor** | TI DRV2625 | 0x5A | Vibrationsalarm (Wecker, Benachrichtigungen) |
| **Temperatur** | ams AS6221 | 0x47 / 0x48 | Hauttemperatur (±0,09°C Genauigkeit!) |
| **Fuel Gauge** | OnSemi LC709205F | 0x0B / 0x55 | Batterie-Ladezustand, Spannung, Strom |
| **PPG/AFE** | *(unbekannt, evtl. Maxim)* | *(direkt/SPI)* | Herzfrequenz, SpO2, EKG |
| **NFC/RFID** | *(unbekannt)* | *(unbekannt)* | Zubehoer-Erkennung (Charger, Puffin-Band) |

### PPG/AFE — Das Herzstueck (woertlich)

Der PPG-Sensor (Photoplethysmographie) ist der wichtigste Sensor. Er misst Herzfrequenz und Blutsauerstoff, indem er Licht durch die Haut schickt und die Reflexion misst.

Aus den Firmware-Strings:
- **"Setting current array to blue %u, green %u, red %u"** — Der AFE hat LEDs in 3 Farben (blau, gruen, rot). Verschiedene Wellenlaengen fuer verschiedene Messungen.
- **"NV Calibration values"** — Es gibt eine Kalibrierung pro Geraet (NV = Non-Volatile = im Flash gespeichert)
- **"ANALYTICS_SIGPROC_VERSION_SIG"** — Die Signal-Processing Library hat eine eigene Versionierung

Der PPG-Sensor scheint ueber SPI angebunden zu sein (nicht I2C wie die anderen). Im Code gibt es 395 verwandte Strings — das ist mit Abstand der komplexeste Treiber.

---

## 4. Die Algorithmen: HR, SpO2, HRV

Die Analyse hat **13 FPU-Regionen** (Floating Point Unit — Fliesskomma-Rechenregionen) identifiziert. Das sind die Stellen im Code, wo die eigentlichen Algorithmen laufen:

```
Region 1:  0x0602AE  (1.958 Bytes, 231 FPU-Ops)  ← HAUPT-ALGORITHMUS
Region 2:  0x061938  (1.066 Bytes,  75 FPU-Ops)
Region 3:  0x061292  (  602 Bytes,  56 FPU-Ops)
Region 4:  0x061602  (  618 Bytes,  48 FPU-Ops)
Region 5:  0x0503AE  (  444 Bytes,  45 FPU-Ops)
...8 weitere kleinere Regionen...
```

**Region 1 (0x0602AE)** ist mit 231 FPU-Operationen in knapp 2 KB der dichteste Algorithmus-Block. Das ist fast sicher die **SigProc Library** — der Kern der HR/SpO2/HRV-Berechnung.

### Was die Algorithmen machen (rekonstruiert)

**Herzfrequenz (HR):**
1. PPG-Sensor liest Licht-Reflexion → rohe Wellenform
2. Bandpass-Filter entfernt Rauschen und Bewegungsartefakte
3. Peak Detection findet Herzschlag-Spitzen
4. Aus den Abstaenden zwischen Peaks → **RR-Intervalle** (Millisekunden)
5. 60.000 / RR-Intervall = **BPM**

**SpO2 (Blutsauerstoff):**
1. Rote und infrarote LED abwechselnd → zwei Wellenformen
2. Verhaeltnis der Amplituden (AC/DC Ratio beider Farben)
3. Dieses "R-Ratio" wird ueber eine Lookup-Tabelle → **SpO2-Prozent** umgerechnet
4. "NV Calibration values" deutet auf individuelle Geraetekalibrierung hin

**HRV (Herzratenvariabilitaet):**
- Basiert auf den RR-Intervallen aus der HR-Berechnung
- Vermutlich **RMSSD** (Root Mean Square of Successive Differences)
- Die FPU-schweren Funktionen nutzen `VSQRT`, `VMUL` — typisch fuer Varianz/Standardabweichung

**Bewegungserkennung:**
- 7 identifizierte Funktionen
- IMU-Daten (Beschleunigung + Gyroskop) → Aktivitaets-Level
- Wird fuer Schlaf-/Wach-Erkennung und Strain-Berechnung genutzt

### Warum der Algorithmus-Code schwer zu lesen ist

Die SigProc Library (die eigentlichen Algorithmen) ist vermutlich ein **separat kompiliertes Binary Blob**, das in die Firmware gelinkt wird. Die Strings dort folgen nicht den normalen ARM Thumb-2 Literal-Pool-Mustern. Das bedeutet: Whoop hat den Algorithmus-Code wahrscheinlich von einem spezialisierten Team/Zulieferer und linkt ihn als Black Box ein.

---

## 5. BLE-Kommunikation

### Das AA01-Protokoll

Alle Kommunikation zwischen App und Strap laeuft ueber ein proprietaeres **AA01-Protokoll**:

```
┌─────┬─────┬──────────┬─────────┬────────┬────────┬───────────────┬───────┐
│ 0xAA│ 0x01│ Laenge   │ Routing │ CRC16  │ Type   │ Payload       │ CRC32 │
│  1B │  1B │  2B (LE) │  2B     │ 2B(LE) │ 1B     │ variable      │ 4B    │
└─────┴─────┴──────────┴─────────┴────────┴────────┴───────────────┴───────┘
```

- **CRC16-MODBUS** schuetzt den Header (6 Bytes)
- **CRC32** schuetzt den gesamten Payload
- Alles Little-Endian, Payloads sind 4-Byte-aligned

### Die 29 BLE-Kommandos (aus der Firmware)

| Kommando | Was es tut |
|---|---|
| `SET_CLOCK` | Uhrzeit setzen |
| `GET_ALARM_TIME` / `SET_ALARM_INFO` / `ALARM_DISABLE` | Smart Alarm |
| `SET_REALTIME_HR` | Echtzeit-HR-Streaming aktivieren |
| `IMU_SET_DATA_STREAM` | IMU-Daten-Streaming |
| `RAW_DATA_STOP` | Streaming stoppen |
| `HAPTICS_RUN_NTF` / `HAPTICS_STOP` | Vibration starten/stoppen |
| `START_FIRMWARE_LOAD` / `LOAD_FW_DATA` / `PROCESS_FIRMWARE_IMAGE` / `VERIFY_FW_IMAGE` | OTA Firmware Update |
| `ECG_MAIN_CONTROL` / `ECG_SELECT_WRIST` / `ECG_SEND_RAW` / `ECG_SAVE_*` | EKG-Aufnahme |
| `UART_ENABLE` / `UART_DISABLE` | Debug UART ein/aus |
| `ADV_NAME_SET` / `GET_ADV_NAME` | BLE-Name aendern |
| `FORGET_BONDING` | Pairing loeschen |
| `SIGPROC_SET_WRIST_DETECT` | Handgelenk-Erkennung |
| `HISTORY_ENABLE_HIGH_FREQ` | Hochfrequenz-Datenaufzeichnung |
| `BODY_LOC_GET_STATUS` | Trageposition abfragen |
| `CONFIG_VALUE_SET_DEVICE_CONFIG` / `START_DEVICE_CONFIG_KEY_EX` / `SEND_NEXT_DEVICE_CONFIG` | Geraetkonfiguration |

### BLE-Services

Die Firmware implementiert standard BLE Services plus proprietaere:
- **Heart Rate Service (HRS)** — Standard BLE HR Broadcasting
- **Battery Service** — Akku-Stand
- **Device Information Service (DIS)** — Geraete-Info
- **Proprietaerer Service** `fd4b0001-...` — AA01-Protokoll (der Hauptkanal)

---

## 6. Firmware-Update Prozess

### Wie ein Update funktioniert

```
1. Whoop App fragt Server: "Gibt's was Neues?"
   POST /firmware-service/v4/firmware/check?deviceName=GOOSE
   → Server antwortet mit neuester Version

2. App laedt Firmware runter
   POST /firmware-service/v4/firmware/version?deviceName=GOOSE
   → Server schickt Base64-kodierte ZIP inline im JSON (!)
   → App entpackt → .zbin Datei

3. App schickt .zbin an Strap via BLE
   START_FIRMWARE_LOAD → LOAD_FW_DATA (in Chunks) → PROCESS → VERIFY

4. Strap schreibt auf externen NOR Flash
   (ISSI oder Winbond 64Mbit SPI NOR Flash als Staging-Area)
   Jeder Chunk wird zurueckgelesen und verglichen

5. Reboot → SBL kopiert von NOR Flash nach MRAM
```

### Das .zbin Format

```
┌──────────────────────────────────────────────┐
│ 512-Byte Header                              │
│   Offset 0x000: Payload CRC32               │
│   Offset 0x004: Komprimierte Groesse        │
│   Offset 0x008: Kompression = gzip (0x05)   │
│   Offset 0x04C: Version "50.35.x.x"        │
│   Offset 0x064: Builder (redacted)           │
│   Offset 0x1F8: Header CRC32               │
│   Offset 0x1FC: Payload CRC32 (Kopie)      │
├──────────────────────────────────────────────┤
│ gzip-komprimierter ARM Binary Payload        │
│   1.059.968 Bytes → 1.548.504 Bytes          │
│   (68,5% Kompressionsrate)                   │
└──────────────────────────────────────────────┘
```

**Drei Integritaets-Checks:**
1. Payload CRC32: `CRC32(payload) == Header[0x000] == Header[0x1FC]`
2. Header CRC32: `CRC32(Header[0x008..0x1F8]) == Header[0x1F8]`
3. Groessen-Check: komprimierte Groesse + 512 == Dateigroesse

---

## 7. Sicherheits-Analyse

### Die grosse Ueberraschung: Keine Krypto-Signaturen

Die Firmware-Validierung besteht **nur aus CRC32**. Keine RSA-Signaturen, keine ECDSA, kein AES, keine Crypto-Library (kein mbedTLS, kein wolfSSL). Das heisst:

| Pruefung | Vorhanden? | Details |
|---|---|---|
| CRC32 Integritaet | Ja | Erkennt Uebertragungsfehler |
| Kryptographische Signatur | **Nein** | Kein Code dafuer gefunden |
| Anti-Rollback | **Nein** | Kein Versions-Vergleich beim Update |
| Stack Canaries | **Nein** | Kein Buffer Overflow Schutz |
| Memory Protection (MPU) | **Nein** | Kein MPU-Code in der App-Firmware |

### Aber: Der SBL ist ein Unbekannter

Es gibt einen **Secondary Boot Loader (SBL)** im ROM des Chips, der die Firmware **vor dem Booten** pruefen koennte. Aus der App-Firmware koennen wir den SBL nicht analysieren — er ist in einem geschuetzten Speicherbereich. Strings deuten auf SBL V1/V2 hin.

**Fazit:** Die Application Firmware selbst hat keine Krypto. Ob der SBL im ROM Signaturen prueft, ist unbekannt und waere nur mit physischem JTAG/SWD-Zugang testbar.

### Cooper BLE Controller Auth

Interessant: Der **Cooper BLE-Controller** (der zweite Prozessor fuer BLE) hat seine eigene Firmware-Authentifizierung:

```
"BLE Controller FW Auth Passed, Continue with FW"
"Clear Cooper Signature, reset Cooper and talk with SBL again"
"Cooper has the incorrect firmware version. Expected version %d.%d.%d.%d"
```

Wenn die Cooper-Auth fehlschlaegt, wird die Signatur geloescht und ueber den SBL neu versucht. Cooper hat also ein rudimentaeres Sicherheitsmodell — die Haupt-Firmware aber nicht.

### Debug-Zugang via BLE

Ein ueberraschend offenes Debug-Interface:
- **UART kann ueber BLE aktiviert werden** (`WSBLE_CMD_UART_ENABLE` / `DISABLE`)
- **14 Debug-Kategorien** im Debug-Menu
- **Memfault** Crash-Reporting integriert (38 verwandte Strings)
- Debug-Kommandos beinhalten: Bootloader-Version anzeigen, Soft-Reset, Flash Erase, RTC manipulieren

### Security Assessment (Zusammenfassung)

```
[HIGH]   CRC32-only Firmware-Auth — modifizierte Firmware koennte geladen werden
[HIGH]   Keine Standard-Crypto-Library — kein mbedTLS/wolfSSL
[MEDIUM] UART Debug via BLE aktivierbar — Informationsleck moeglich
[MEDIUM] Extensives Debug-Menu — Reset, Assert, Parameter-Aenderung
[LOW]    Memfault Crash-Daten — koennten sensible Infos enthalten
[LOW]    Kein MPU — kein Memory-Schutz zwischen Tasks
[LOW]    Keine Stack Canaries — Buffer Overflows unerkannt
[INFO]   Cooper BLE hat eigene Auth — zumindest der BLE-Stack ist geschuetzt
```

---

## 8. Architektur-Diagramm

```
┌─────────────────────────────────────────────────────────────────────┐
│                  QP RTOS — Active Object Framework                   │
│                                                                       │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────┐  ┌───────────┐ │
│  │  SUPERVISOR  │  │   BLE AO     │  │  BLE CMD AO  │  │ CORDIO AO │ │
│  │  (System-    │  │  (Stack-     │  │  (AA01-       │  │ (BLE      │ │
│  │   Manager)   │  │   Manager)   │  │   Protokoll)  │  │  Stack)   │ │
│  └──────┬───────┘  └──────────────┘  └──────────────┘  └───────────┘ │
│         │                                                              │
│  ┌──────┴──────┐  ┌──────────────┐  ┌──────────────┐  ┌───────────┐ │
│  │  SENSORS AO │→ │  ANALYTICS   │→ │   FLASH AO   │  │  I2C AO   │ │
│  │  (PPG, IMU, │  │  (SigProc:   │  │  (Circular   │  │  (Multi-  │ │
│  │   Daten)    │  │   HR/SpO2)   │  │   Buffer)    │  │   Bus)    │ │
│  └─────────────┘  └──────────────┘  └──────────────┘  └─────┬─────┘ │
│                                                               │       │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────┐        │       │
│  │ FUEL GAUGE  │  │  LISTENER    │  │ TEMP SENSORS │        │       │
│  │ LC709205F   │  │  (Laden/WPT) │  │   AS6221     │←───────┤       │
│  └─────────────┘  └──────────────┘  └──────────────┘        │       │
│                                                               │       │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────┐        │       │
│  │ UI MANAGER  │  │   LED UI     │  │  HAPTICS AO  │        │       │
│  │ (Orchestr.) │  │   LP5562     │  │   DRV2625    │←───────┤       │
│  └─────────────┘  └──────────────┘  └──────────────┘        │       │
│                                                               │       │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────┐        │       │
│  │ TAG READER  │  │  ECG CTRL    │  │ DEBUG MENU   │        │       │
│  │ (RFID/NFC)  │  │  (EKG)       │  │  (UART)      │←───────┘       │
│  └─────────────┘  └──────────────┘  └──────────────┘                 │
└─────────────────────────────────────────────────────────────────────┘
                              │
         ┌────────────────────┼─────────────────────┐
         ▼                    ▼                      ▼
   ┌───────────┐      ┌────────────┐         ┌───────────┐
   │  Apollo4  │      │  Cooper    │         │ Externer  │
   │  Hardware │      │  BLE Radio │         │ NOR Flash │
   │  (GPIO,   │      │  (Cortex-  │         │ (64 Mbit  │
   │   UART,   │      │   M0)      │         │  Staging) │
   │   SPI,    │      └────────────┘         └───────────┘
   │   ADC)    │
   └───────────┘
```

---

## 9. Projekt-Struktur der Original-Firmware

Aus den eingebetteten Source-Pfaden konnten wir die Whoop Firmware-Projektstruktur rekonstruieren:

```
whoop-firmware/
  src/
    supervisor_ao.c         # System-Manager
    i2c_ao.c                # I2C Bus-Treiber
    flash.c                 # Flash/Storage
    itest_ao.c              # Manufacturing Tests
    as6221_ao.c             # Temperatur-Chip
    temp_sensors_ao.c       # Temperatur-Logic
    whoop_cordio/
      whoop_cordio_ao.c     # BLE Stack

  modules/
    ble/src/
      ble_cmd_ao.c          # BLE Command Handler
    sensors/src/
      sensors_ao.c          # Sensor-Datenerfassung
    analytics/src/
      analytics_ao.c        # Signal Processing
    fuel_gauge/onsemi_fuel_gauge/src/
      onsemi_fuel_gauge_ao.c
      lc709205f_ao.c
    ui_subsystem/src/
      ui_manager_ao.c
      led_ui_ao.c
      lp5562_ao.c
      haptics_ao.c
      drv2625_ao.c
    wpt/src/
      listener_ao.c         # Wireless Power Transfer
    tag_reader/src/
      tag_reader_ao.c       # RFID/NFC
    ecg_control/src/
      ecg_control_ao.c      # EKG
    debug_menu/src/
      debugmenu_ao.c        # Debug-Konsole

  test/integration_test/
    itest_listener_ao.c
    itest_temp_sensors_ao.c
    itest_fuel_gauge_ao.c
    itest_ui_manager_ao.c
```

---

## 10. Interessante Firmware-Strings

Einige Highlights aus den 20.075 Strings:

**Boot & System:**
```
"WATCHDOG enabled with a period of %u seconds"
"ERROR: Failed to enable rtc"
"!! WATCHDOG reset occurred last run!!"
"Fast Recovery Mode set to: %d"
```

**Sensoren:**
```
"Setting current array to blue %u, green %u, red %u"
"NV Calibration values failed to set"
"Too many attempts at reading NV calibration values, giving up"
```

**BLE:**
```
"Phone connected: %s"
"All connections disconnected"
"Puffin connected, using puffin double tap signal."
"Started advertising with unrecognized interval: %u"
```

**Firmware Update:**
```
"CRC of update image passed"
"CRC of update image failed"
"Update Flash: ISSI 64Mb NOR detected"
"Update Flash: Winbond 64Mb NOR detected"
"Update Flash: Write failed at addr 0x%x"
```

**Cooper BLE:**
```
"BLE Controller FW Auth Passed, Continue with FW"
"Clear Cooper Signature, reset Cooper and talk with SBL again"
"Cooper has the incorrect firmware version. Expected version %d.%d.%d.%d"
```

**Debug:**
```
"b           Show bootloader firmware version and build meta data"
"v           Show main firmware versions"
"M           Reboot using soft reset GPIO"
"N           Hard erase entire flash device"
"O           shutdown ble controller processor [DESTRUCTIVE]"
"P           Reset BLE Controller"
```

---

## 11. Was bedeutet das fuer Custom Firmware?

### Status: Theoretisch moeglich, praktisch unsicher

**Dafuer spricht:**
- Keine kryptographischen Signaturen in der Application Firmware
- CRC32-only Validierung — CRC32 ist trivial zu berechnen
- Kein Anti-Rollback — aeltere/modifizierte Versionen sollten akzeptiert werden
- Wir haben einen funktionierenden .zbin Builder (alle CRC-Checks bestanden)
- Ein "Hello World" Custom-Firmware kompiliert sauber (668 Bytes)

**Dagegen spricht:**
- Der **SBL im ROM** koennte trotzdem Signaturen pruefen — wir koennen das von aussen nicht sehen
- `auth_algo=1` und `auth_key_idx=0x0D` im Header sind suspekt — das KOENNTE auf eine Signatur-Pruefung im SBL hindeuten
- Wenn der SBL prueft und ablehnt: Recovery-Optionen unklar (Brick-Risiko)
- Cooper BLE Controller hat eigene Auth — auch der muesste ggf. akzeptieren

### Naechste Schritte (fuer die Mutigen)

1. **JTAG/SWD Zugang** — physisch an die Test-Pads, SBL dumpen, analysieren
2. **SBL-Verhalten testen** — Custom .zbin via BLE schicken, Fehler-Response analysieren (ohne zu flashen)
3. **Firmware-Patching** — Statt komplett eigene FW: Original-Binary gezielt modifizieren (z.B. Debug-Strings aendern), CRC neu berechnen, schauen ob es bootet
4. **Downgrade-Test** — Aeltere offizielle Version flashen, pruefen ob Anti-Rollback aktiv ist

---

*Basiert auf Analyse von maverick-50.35.2.0.bin (1.548.504 Bytes)*
*Tools: radare2, angr, capstone, arm-none-eabi-gcc | 6 Analyse-Tracks, ~13 MB JSON-Output*
