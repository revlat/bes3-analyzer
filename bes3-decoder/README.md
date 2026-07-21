# bes3-decoder

Gemeinsame Dekodier-Logik für die verifizierten BES3-CAN-FD-Signale
(Varint-/Protobuf-Wire-Format, siehe [Haupt-README](../README.md) für Details
zum Frame-Aufbau und die Herleitung jeder Skala). Kein eigenständiges Tool,
sondern eine Python-Bibliothek — nutzbar sowohl für **ganze aufgezeichnete
Dateien** als auch für **einzelne Live-CAN-Nachrichten**. Wird u. a. von
[`bes3-log-plotter`](../bes3-log-plotter/README.md) verwendet.

Stand: 2026-07-21 — siehe „Aktualität" unten.

## Enthält (`decode_bes3.py`)

- `SIGNALS`: Dict `signalname -> (Entry-ID-Hex, Dekodierfunktion)` — die
  Registry aller bekannten Werte. Maßgebliche, laufend gepflegte Tabelle
  (inkl. Herleitung/Belege) ist die [Haupt-README](../README.md); hier steckt
  dieselbe Zuordnung nur als lauffähiger Code.
- `decode_frame(data: bytes) -> dict`: dekodiert **eine einzelne** rohe
  CAN-FD-Frame-Payload (z. B. `msg.data` von `python-can`) und gibt
  `{signalname: wert}` für alle in genau diesem einen Frame gefundenen
  Signale zurück (leeres Dict, wenn keins passt). **Zustandslos** — jeder
  Aufruf ist unabhängig von vorherigen Frames.
- `decode_file(path) -> dict`: liest eine CSV (`Timestamp,ID,DLC,Data`, wie
  sie [`bes3-logger`](../bes3-logger/README.md) schreibt), ruft intern
  `decode_frame` je Zeile auf und sammelt die Treffer zu Zeitreihen
  `{signalname: [(timestamp, wert), ...]}`.
- `extract_mode_list(path) -> dict`: liest die (bike-spezifische,
  konfigurierbare) Fahrmodus-Namensliste aus einer Aufnahme, siehe
  „Fahrmodi" in der Haupt-README.
- `extract_ascii_strings(path, min_count=3) -> dict`: findet wiederkehrende
  druckbare ASCII-Teilstrings über die ganze Aufnahme — Bauteil-Typcodes,
  Seriennummern, Parameter-Codes, Region-/Speed-Limit-Strings, Teile-
  /Materialnummern etc., siehe „Klartext-Kennungen" in der Haupt-README.
  Generischer `strings(1)`-artiger Scan (keine Interpretation der Bedeutung)
  — **aber** jeder String wird mit der Menge der CAN-IDs zurückgegeben, in
  denen er auftaucht. Ein String klebt i. d. R. an einem festen, stabilen
  CAN-ID-Set (z. B. taucht ein Bluetooth-Gerätename immer nur in einer
  bestimmten ID auf, eine Teilenummer immer nur in ein, zwei anderen) — das
  ist der Anhaltspunkt, um ohne weitere Dekodierung grob einzuordnen, um was
  für eine Art String es sich handelt, statt nur eine undifferenzierte
  Liste zu haben. Gefiltert auf Strings, die in mindestens `min_count`
  verschiedenen Frames auftauchen — Zufallstreffer im MAC-Rauschen der
  ersten 12 Header-Byte tauchen so gut wie nie zweimal identisch auf und
  fallen damit raus. Gibt `{string: {"count": n, "can_ids": {id, ...}}}`
  zurück.
- `describe_can_ids(can_ids) -> str`: ordnet eine Menge CAN-IDs (aus
  `extract_ascii_strings`) einer bekannten Domäne zu, wenn eine der IDs in
  `CAN_ID_HINTS` bekannt ist (z. B. `613` → Ladegerät-Seriennummer, `603` →
  Akku-Seriennummer, `401`/`3C2`/`409` → Konfig-/Parameter-Tabelle — siehe
  „Klartext-Kennungen" bzw. „Ladegerät-/Akku-Domäne" in der Haupt-README),
  zeigt aber immer auch die rohen IDs mit an. Reine Einordnungshilfe
  basierend auf Beobachtung, keine offizielle Spezifikation.
- `KNOWN_COMPONENT_CODES`: Dict der vier bekannten Bosch-Bauteil-Typcodes
  (`BDU3740`, `BBP3770`, `BCM3100`, `BHU3600`) → Klartext-Bauteilname, zum
  Abgleich gegen `extract_ascii_strings()`-Ergebnisse.

`decode_file` ist also nur eine dünne Schleife um `decode_frame` — **derselbe
Dekodier-Code läuft identisch für eine ganze Datei wie für eine einzelne live
empfangene Nachricht.**

## Nutzung 1: ganze Datei auswerten

Als Bibliothek, z. B. so wie [`bes3-log-plotter`](../bes3-log-plotter/) es tut:

```python
import decode_bes3 as d

res = d.decode_file("testfahrt3_completely_full.csv")   # {signalname: [(ts, wert), ...]}
modes = d.extract_mode_list("testfahrt3_completely_full.csv")
print(res["geschwindigkeit_kmh"][-1])   # letzter Geschwindigkeitswert: (timestamp, km/h)
```

Oder direkt auf der Kommandozeile als schneller Text-Check ohne Plot:

```bash
python3 decode_bes3.py testfahrt3_completely_full.csv
```

Gibt je Signal eine Zusammenfassung aus (Anzahl Datenpunkte, Min/Max, erster/
letzter Wert bzw. beim Fahrmodus den Wechselverlauf), gefolgt von den
erkannten Bauteil-Typcodes und weiteren wiederkehrenden Text-Strings. Keine
Abhängigkeiten außer der Python-Standardbibliothek.

`extract_ascii_strings()` eignet sich auch unabhängig vom Rest, z. B. um
schnell zu prüfen, welches Bike/welche Komponenten-Revision eine Aufnahme
stammt:

```python
strings = d.extract_ascii_strings("testfahrt3_completely_full.csv")
for code, label in d.KNOWN_COMPONENT_CODES.items():
    if code in strings:
        print(code, "->", label)
```

[`bes3-log-plotter`](../bes3-log-plotter/) nutzt genau das für sein
Text-Panel.

## Nutzung 2: einzelne Live-Nachrichten dekodieren

`decode_frame()` braucht keine Datei — sie nimmt die rohen Bytes **einer**
CAN-FD-Nachricht entgegen und dekodiert sofort, was darin steckt. Direkt
kombinierbar mit `python-can` (dieselbe Bus-Anbindung wie in
[`bes3-logger`](../bes3-logger/README.md)), z. B. für eine simple
Live-Ausgabe auf der Konsole:

```python
import can
import decode_bes3 as d

with can.Bus(interface="usbtingo", bitrate=500000, data_bitrate=2000000, fd=True) as bus:
    while True:
        msg = bus.recv(timeout=1.0)
        if msg is None:
            continue
        for name, wert in d.decode_frame(msg.data).items():
            print(f"{name}: {wert}")
```

Jede empfangene Nachricht kann **null, ein oder mehrere** Signale enthalten
(ein CAN-FD-Frame bündelt oft mehrere Tag-Value-Einträge) — `decode_frame`
gibt entsprechend ein Dict mit allen Treffern in genau dieser einen
Nachricht zurück.

Voraussetzungen für dieses Beispiel wie beim Logger: `python-can` +
`python-can-usbtingo`, siehe „Womit" in der
[`bes3-logger`-README](../bes3-logger/README.md#womit-hardware--software)
(inkl. venv-Anleitung für Linux/Windows und dem Hinweis zur nötigen
udev-Regel unter Linux).

**Hinweis:** Das obige Beispiel ist ein Minimalbeispiel, keine fertige
Live-Anzeige/Dashboard-Lösung — ein ausgebauter Live-Dekoder mit richtiger
Anzeige ist als mögliches künftiges Projekt angedacht, siehe „Ausblick:
Live-Dekoder" in der
[`bes3-log-plotter`-README](../bes3-log-plotter/README.md#ausblick-live-dekoder).

## Aktualität

`SIGNALS` spiegelt den heutigen Stand der bekannten/verifizierten Signale.
Maßgeblich und laufend gepflegt ist die Tabelle in der
[Haupt-README](../README.md); wird dort etwas Neues verifiziert, sollte
`SIGNALS` hier bei Gelegenheit nachgezogen werden (das Mapping ist bewusst
simpel gehalten: Entry-ID + kleine Dekodierfunktion pro Signal).

## Lizenz

MIT — siehe [LICENSE](../LICENSE) im übergeordneten Ordner.
