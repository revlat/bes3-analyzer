# bes3-canfd-logger

Kleines Skript, das den CAN-FD-Bus des Bosch-eBike-Smart-Systems mitschneidet
und roh (Timestamp, CAN-ID, DLC, Daten als Hex) in eine CSV-Datei schreibt.
Read-only — es werden keine Nachrichten gesendet.

Basiert auf dem Skript, das ich als User laoli am 06.10.2024 hier
veröffentlicht hatte: https://www.pedelecforum.de/forum/index.php?threads/bosch-smart-system-can-message-ids.107771/

Zum Frame-Aufbau und zur Dekodierung der geloggten Daten siehe die
[README](../README.md) im übergeordneten Ordner.

## Womit (Hardware & Software)

- **CAN-FD-USB-Adapter**: [usbTingo](https://www.fischl.de/usbtingo/) — im
  Skript fest als `interface="usbtingo"` konfiguriert, **500 kBaud** nominal /
  **2000 kBaud** Datenrate (die Bus-Parameter des Bosch-Smart-Systems).
- **Python** 3 mit den Paketen [`python-can`](https://python-can.readthedocs.io/)
  und [`python-can-usbtingo`](https://github.com/EmbedME/python-can-usbtingo)
  (liefert das python-can-Interface-Plugin):
  ```bash
  pip install python-can python-can-usbtingo
  ```
  Alternativ in einem **virtuellen Environment** (empfohlen — unter Linux
  bei vielen aktuellen Distros sogar nötig, da `pip install` systemweit
  wegen PEP 668 „externally-managed-environment" verweigert wird):

  **Linux/macOS:**
  ```bash
  python3 -m venv .venv
  .venv/bin/pip install python-can python-can-usbtingo
  .venv/bin/python bes3-canfd-logger.py
  ```

  **Windows (PowerShell):**
  ```powershell
  python -m venv .venv
  .venv\Scripts\pip install python-can python-can-usbtingo
  .venv\Scripts\python bes3-canfd-logger.py
  ```
- Läuft auf jedem Rechner mit freiem USB-Port, typischerweise ein
  **Raspberry Pi** am Rad. Auf dem Pi wird zusätzlich die Onboard-LED (`led0`)
  genutzt (siehe unten); unter Windows ertönt stattdessen ein Signalton. Auf
  anderen Systemen (z. B. normalem Linux-PC) läuft es ohne LED/Ton einfach
  weiter.

### Linux (getestet auf openSUSE Tumbleweed): USB-Berechtigung für das usbTingo (udev-Regel)

Ohne weitere Einrichtung meldet `can.Bus(...)` als normaler (Nicht-root-)User
auf openSUSE Tumbleweed `usb1.USBErrorAccess: LIBUSB_ERROR_ACCESS`, weil
libusb ohne passende udev-Regel keinen Zugriff auf das Gerät bekommt (auf
anderen Distros mit ähnlicher Standard-udev-Konfiguration ist derselbe Fehler
zu erwarten). Abhilfe schafft eine einmalige udev-Regel, die dem Gerät für
alle User Lese-/Schreibrechte gibt (idVendor `1fc9` / idProduct `8320` sind
die USBtingo-Kennungen, sichtbar z. B. via `dmesg` oder `lsusb`):

```bash
echo 'SUBSYSTEM=="usb", ATTR{idVendor}=="1fc9", ATTR{idProduct}=="8320", MODE="0666"' \
  | sudo tee /etc/udev/rules.d/99-usbtingo.rules
sudo udevadm control --reload-rules
sudo udevadm trigger
```

Danach das usbTingo einmal ab- und wieder anstecken (oder neu booten) —
danach läuft `bes3-canfd-logger.py` ohne `sudo` und ohne Zugriffsfehler.

## Nutzung

```bash
python3 bes3-canfd-logger.py [--prefix canfd]
```

- `--prefix` (optional, Standard `canfd`): Präfix für den Dateinamen, z. B. um
  Aufnahmen einer bestimmten Fahrt oder eines Tests zu benennen
  (`--prefix testfahrt5`).
- Aufzeichnung starten: Skript ausführen, warten bis
  `Starte CAN-Datenaufzeichnung nach ... ` erscheint.
- Aufzeichnung beenden: `Strg+C`. Die Datei ist zu diesem Zeitpunkt bereits
  vollständig auf der Festplatte (siehe „Ausfallsicherheit" unten) — ein
  sauberes Beenden ist für die Datenintegrität nicht erforderlich.
- Nach 10 Sekunden Laufzeit blinkt auf dem Raspberry Pi einmalig die
  Onboard-LED auf (unter Windows ertönt ein Signalton) — als visuelle/
  akustische Bestätigung, dass die Aufzeichnung tatsächlich läuft, ohne dass
  man aufs Terminal schauen muss.

## Ausgabedatei

Es wird **nur eine** CSV-Datei geschrieben — die vollständige, ungefilterte
Aufzeichnung **jeder** empfangenen Nachricht (keine Dupletten-Erkennung, keine
Ausdünnung/Reduktion). Frühere Versionen des Loggers schrieben zusätzlich eine
duplikat-gefilterte sowie mehrere ausgedünnte Varianten (`_reduced_10/100/…`)
— das entfällt jetzt bewusst, da für die Analyse ohnehin immer die vollständige
Aufzeichnung verwendet wird.

**Dateiname**: wird bei **jedem Start automatisch** aus Präfix, aktuellem
Datum/Uhrzeit und einer vierstelligen Zufallszahl gebildet, z. B.:

```
canfd_20260721-193045_4821_completely_full.csv
```

Dadurch überschreibt keine Aufzeichnung eine vorherige — auch nicht, wenn ein
Raspberry Pi ohne RTC nach einem Stromausfall mit zurückgesetzter Systemzeit
neu startet (die Zufallszahl verhindert Kollisionen, selbst wenn mehrere
Aufnahmen zufällig denselben Zeitstempel bekämen).

CSV-Format: `Timestamp,ID,DLC,Data` (siehe Haupt-README für Details zum
Frame-Inhalt).

## Ausfallsicherheit

Der Logger **flusht dauerhaft**: nach **jeder einzelnen** empfangenen
Nachricht wird sowohl `flush()` (Python-Puffer → Betriebssystem) als auch
`os.fsync()` (Betriebssystem-Puffer → tatsächlich auf den Datenträger)
aufgerufen. Dadurch ist die Aufzeichnung bis zur zuletzt empfangenen
CAN-Nachricht garantiert auf der Festplatte/SD-Karte gespeichert — auch wenn
der Log-Rechner (z. B. der Raspberry Pi am Rad) mitten in der Fahrt ohne
sauberes Herunterfahren ausfällt (Stromverlust, Absturz, Reset). Es geht dabei
höchstens die zuletzt *in Bearbeitung* befindliche Nachricht verloren, nicht
der bisherige Aufzeichnungsverlauf.

Das kostet etwas Durchsatz gegenüber gepuffertem Schreiben, ist aber bei den
hier anfallenden Datenraten unkritisch und für ein Aufnahmegerät ohne
unterbrechungsfreie Stromversorgung die richtige Abwägung.

## Lizenz

MIT — siehe [LICENSE](../LICENSE) im übergeordneten Ordner.
