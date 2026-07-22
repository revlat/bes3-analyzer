# bes3-ble-logger

Kleines Skript, das die BLE-Notifications des Bosch-eBike-Smart-Systems
mitschneidet und roh (Timestamp + Hex-Frame) in eine Textdatei schreibt.
Read-only — es werden keine Nachrichten gesendet, keine Verbindung
manipuliert.

Analog zu [`bes3-canfd-logger`](../bes3-canfd-logger/README.md) (dort: CAN-FD-Bus), nur
für den BLE-Transportweg desselben Systems. Zum BLE-Frame-Aufbau und zur
Dekodierung der geloggten Daten siehe „BLE-Unterstützung" in der
[`bes3-decoder`-README](../bes3-decoder/README.md#ble-unterstützung).

**Stand jetzt: reines Aufzeichnen.** `bes3-ble-logger.py` dekodiert selbst
noch nichts — es schreibt nur die rohen Notification-Bytes mit Zeitstempel
weg. Zum Dekodieren/Plotten die aufgezeichnete Datei anschließend mit
[`bes3-decoder/decode_bes3.py`](../bes3-decoder/README.md) bzw.
[`bes3-log-plotter`](../bes3-log-plotter/README.md) verarbeiten — das
Ausgabeformat ist bewusst so gewählt, dass beide es direkt ohne
Zusatzarbeit einlesen können (siehe „Ausgabedatei" unten).

## ⚠ Bekannte Einschränkung: Live-Verbindung noch nicht zuverlässig

**Stand jetzt in der Praxis noch nicht demonstriert:** eine stabile
BLE-Verbindung zum Bike von einem zweiten, unabhängigen Gerät (z. B. einem
Laptop) herzustellen, während das Bike bereits mit einem anderen Gerät
(Handy mit Bosch-eBike-Flow-App) gekoppelt ist/war. Beim ersten echten Test
gegen ein reales Bike zeigte sich:

- Das Bike lässt vermutlich **absichtlich nur eine aktive Verbindung
  gleichzeitig** zu.
- Es scheint eine **Kopplung (Pairing/Bonding)** vorauszusetzen, bevor
  überhaupt eine Verbindung zustande kommt — ein offenes, unauthentifiziertes
  GATT-Connect (wie in `ble_connector.py` aktuell implementiert) reichte
  nicht.
- Selbst nach erfolgreicher Kopplung über die Windows-Bluetooth-Einstellungen
  konnte `bleak` das Gerät weder per Name noch per Adresse finden/verbinden —
  vermutlich, weil es eine rotierende, private BLE-Adresse nutzt, die nur der
  jeweils gebondete Host auflösen kann, und Windows das nicht transparent an
  `bleak`/WinRT weiterreicht.

Der hier implementierte Ansatz (unabhängiger zweiter BLE-Client neben der
Bosch-App) ist also **so noch nicht praxistauglich** — wird weiter
untersucht. Ein möglicher Ausweg, der noch nicht umgesetzt/verifiziert ist:
auf demselben Gerät mitlesen, das ohnehin schon verbunden ist (z. B. ein
Bluetooth-HCI-Snoop-Log vom Handy mit der Bosch-App, dann offline
auswerten), statt eine zweite, unabhängige Verbindung aufzubauen.

`ble_connector.py`/`bes3-ble-logger.py` selbst sind fertig implementiert und
strukturell getestet (Simulation mit gefaktem `bleak`) — der reale
Verbindungsaufbau zu einem echten Bike ist aber aktuell nicht zuverlässig.

## Aufbau: `ble_connector.py` + `bes3-ble-logger.py`

Die eigentliche Verbindungslogik (Scannen nach `DEVICE_NAME`, Verbinden,
`CHAR_UUID_NOTIFY` abonnieren, automatischer Reconnect bei Abbruch — siehe
„Verbindungsabbruch & Reconnect" unten) steckt in **`ble_connector.py`** als
Async-Generator `stream_ble_notifications()` — liefert
`(sekunden_seit_erstem_start, rohe_bytes)` pro Notification, sonst nichts
(keine Dekodierung, keine Datei-I/O). `bes3-ble-logger.py` ist nur ein
dünner CLI-Wrapper darum, der die gelieferten Bytes in eine Datei schreibt.

Grund für die Trennung: ein künftiges **Live-Dashboard** (Dekodierung +
Visualisierung während der Aufzeichnung, statt nur nachträglich aus der
Datei) kann denselben Generator importieren und direkt an
`decode_bes3.decode_ble_frame()` weiterreichen, ohne die
Verbindungs-/UUID-Logik ein zweites Mal zu schreiben — eine einzige Quelle
der Wahrheit für Gerätename/UUIDs, analog zum `SIGNALS`-Prinzip in
`bes3-decoder`:

```python
import ble_connector as c
import decode_bes3 as d

async for ts, data in c.stream_ble_notifications():
    for name, wert in d.decode_ble_frame(data).items():
        print(f"{ts:.3f}s  {name}: {wert}")
```

## Verbindungsdaten (per nRF Connect ermittelt)

- **Beworbener Gerätename:** `smart system eBike`
- **Service-UUID:** `00000010-eaa2-11e9-81b4-2a2ae2dbcce4`
- **Characteristic-UUID (Notify):** `00000011-eaa2-11e9-81b4-2a2ae2dbcce4`

Diese Werte sind in `ble_connector.py` fest hinterlegt (`DEVICE_NAME`,
`SERVICE_UUID`, `CHAR_UUID_NOTIFY`).

## Womit (Hardware & Software)

- **Bluetooth LE** — auf jedem Laptop mit eingebautem oder externem
  BLE-Adapter lauffähig (kein spezieller USB-Adapter nötig wie beim
  CAN-FD-Logger).
- **Python** 3 mit dem Paket [`bleak`](https://bleak.readthedocs.io/)
  (plattformübergreifende BLE-Bibliothek für Windows/Linux/macOS):
  ```bash
  pip install bleak
  ```
  Alternativ in einem **virtuellen Environment** (empfohlen — unter Linux
  bei vielen aktuellen Distros sogar nötig, siehe PEP 668):

  **Windows (PowerShell):**
  ```powershell
  python -m venv .venv
  .venv\Scripts\pip install bleak
  .venv\Scripts\python bes3-ble-logger.py
  ```

  **Linux/macOS:**
  ```bash
  python3 -m venv .venv
  .venv/bin/pip install bleak
  .venv/bin/python bes3-ble-logger.py
  ```
- Unter Windows nutzt `bleak` die eingebaute WinRT-Bluetooth-API — keine
  zusätzliche Treiber-/Rechteeinrichtung nötig (anders als die udev-Regel für
  das usbTingo beim CAN-FD-Logger). Unter Linux läuft `bleak` über BlueZ
  (D-Bus) — normalerweise ohne root nutzbar, sofern der Bluetooth-Dienst
  läuft.
- Voraussetzung: das Bike ist eingeschaltet und **nicht bereits mit einem
  anderen Gerät verbunden** (z. B. der Bosch-eBike-Flow-App am Handy) — das
  Bike lässt vermutlich nur eine aktive Verbindung gleichzeitig zu. In der
  Praxis reichte das bisher aber **nicht** aus, um zuverlässig zu verbinden
  — siehe „Bekannte Einschränkung" oben.

## Nutzung

```bash
python3 bes3-ble-logger.py [--prefix ble] [--scan-timeout 10] [--reconnect-delay 3]
```

- `--prefix` (optional, Standard `ble`): Präfix für den Dateinamen.
- `--scan-timeout` (optional, Standard `10` Sekunden): wie lange **pro
  Versuch** nach dem Gerät gesucht wird. Wird es nicht gefunden, bricht das
  Skript **nicht** ab, sondern versucht es nach `--reconnect-delay` erneut
  (siehe unten).
- `--reconnect-delay` (optional, Standard `3` Sekunden): Wartezeit vor einem
  erneuten Verbindungsversuch nach einem Abbruch/Fehler/nicht gefundenem
  Gerät.
- Aufzeichnung starten: Skript ausführen, warten bis „Aufzeichnung läuft
  nach ... " erscheint. Jede empfangene Notification wird zusätzlich live
  auf der Konsole ausgegeben (Zeit + Hex-Bytes, noch unde­kodiert).
- Aufzeichnung beenden: nur per `Strg+C` — alles andere (Gerät nicht
  gefunden, Verbindung verloren) führt zu einem automatischen erneuten
  Versuch statt zum Abbruch.

## Verbindungsabbruch & Reconnect

BLE-Notify-Abos haben kein Ablaufdatum — einmal abonniert, bleiben sie
bestehen, solange die Verbindung lebt; es gibt kein „nach X Sekunden erneut
abonnieren". Bricht die Verbindung aber unerwartet ab (Bike außer
Reichweite/ausgeschaltet, Akku leer, Störung) oder wird das Gerät beim
Scannen nicht gefunden, versucht `stream_ble_notifications()` **automatisch
erneut zu verbinden** (Scan → Connect → Notify neu abonnieren), statt
stillschweigend zu verstummen oder abzubrechen:

- Jeder Fehlschlag wird auf der Konsole gemeldet, danach `--reconnect-delay`
  Sekunden gewartet, dann ein neuer Versuch gestartet — endlos, bis es
  wieder klappt.
- Die Zeitachse (`ts`) bleibt dabei durchgehend: sie bezieht sich auf den
  **allerersten** Start, nicht auf die jeweils aktuelle Verbindung — springt
  bei einem Reconnect also nicht auf 0 zurück (eine Lücke während der
  Trennung in den Zeitstempeln ist normal, kein Fehler).
- Beendet wird der Generator ausschließlich durch den Aufrufer selbst
  (`Strg+C` bzw. `gen.aclose()`) — das wird intern anders behandelt als ein
  Verbindungsfehler, damit ein gewollter Abbruch nicht fälschlich als Fehler
  interpretiert und endlos weiterversucht wird.

## Ausgabedatei

**Dateiname**: wird bei jedem Start automatisch aus Präfix, aktuellem
Datum/Uhrzeit und einer vierstelligen Zufallszahl gebildet (verhindert
Überschreiben vorheriger Aufnahmen), z. B.:

```
ble_20260722-193045_4821_hex.txt
```

**Format**: eine Zeile pro Notification, `<sekunden seit Skriptstart>
<hex-bytes mit '-' getrennt>`, z. B.:

```
0.482103 30-04-98-2D-08-56
1.101987 30-02-98-5A
```

Das ist exakt das Format, das `bes3-decoder.is_ble_log()`/`decode_ble_file()`
und `bes3-log-plotter` bereits unterstützen (Timestamp durch Leerzeichen vom
Frame getrennt, siehe „BLE-Unterstützung" in der
[`bes3-decoder`-README](../bes3-decoder/README.md#ble-unterstützung)) —
die Aufzeichnung kann also direkt weiterverwendet werden:

```bash
python3 ../bes3-decoder/decode_bes3.py ble_20260722-193045_4821_hex.txt
python3 ../bes3-log-plotter/bes3-log-plotter.py ble_20260722-193045_4821_hex.txt
```

## Ausfallsicherheit

Wie beim CAN-FD-Logger wird nach **jeder einzelnen** Notification sowohl
`flush()` als auch `os.fsync()` aufgerufen — die Aufzeichnung ist damit bis
zur zuletzt empfangenen Notification garantiert auf der Festplatte
gespeichert, auch bei einem Absturz/Abbruch mitten in der Aufzeichnung.

## Grenzen / Nächste Schritte

- `bes3-ble-logger.py` selbst dekodiert/visualisiert nichts live — dafür
  entweder die aufgezeichnete Datei nachträglich mit
  `bes3-decoder`/`bes3-log-plotter` verarbeiten, oder (für ein künftiges
  Live-Dashboard) direkt `ble_connector.stream_ble_notifications()`
  importieren und mit `decode_ble_frame()` kombinieren (siehe oben).
- Reconnect ist unbegrenzt und ohne Backoff (immer derselbe
  `--reconnect-delay`) — kein Aufgeben nach N Versuchen, keine wachsenden
  Wartezeiten.
- Read-only: es werden ausschließlich Notifications empfangen, nie
  geschrieben — kein Steuern/Manipulieren des Bikes.

## Lizenz

MIT — siehe [LICENSE](../LICENSE) im übergeordneten Ordner.
