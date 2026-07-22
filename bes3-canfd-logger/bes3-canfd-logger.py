import can
import csv
import time
import os
import random
import argparse
from datetime import datetime

# Prüfe, ob das Skript auf einem Windows-System läuft
if os.name == 'nt':  # 'nt' steht für Windows
    import winsound

# Funktion zum Steuern der Raspberry Pi LED
def set_led(state):
    if os.path.exists("/sys/class/leds/led0/brightness"):
        with open("/sys/class/leds/led0/brightness", "w") as led_file:
            led_file.write("1" if state else "0")

# Wenn das Skript auf einem Raspberry Pi läuft, setze die LED-Steuerung auf "none"
if os.path.exists("/sys/class/leds/led0/trigger"):
    with open("/sys/class/leds/led0/trigger", "w") as trigger_file:
        trigger_file.write("none")

# Argumente parsen
parser = argparse.ArgumentParser(description="CAN Bus Data Logger")
parser.add_argument('--prefix', type=str, default='canfd', help='Dateipräfix für die Ausgabedatei (Standard: canfd)')
args = parser.parse_args()

# Dateiname wird bei jedem Start neu aus Datum+Uhrzeit+Zufallszahl gebildet, damit
# aufeinanderfolgende Aufnahmen sich nie überschreiben (auch wenn die Systemzeit
# z. B. nach einem Stromausfall ohne RTC zurückgesetzt wurde, sorgt die
# Zufallszahl trotzdem für Eindeutigkeit).
timestamp_str = datetime.now().strftime("%Y%m%d-%H%M%S")
random_suffix = random.randint(0, 9999)
csv_filename = f"{args.prefix}_{timestamp_str}_{random_suffix:04d}_completely_full.csv"

# Startzeit für die Aufzeichnung
start_time = time.time()

# Öffne den CAN-Bus und initialisiere die CSV-Datei.
# mode='x' statt 'w': schlägt hart fehl statt eine bestehende Datei stillschweigend
# zu überschreiben (sollte durch Zeitstempel+Zufallszahl ohnehin nie vorkommen).
with can.Bus(interface="usbtingo", bitrate=500000, data_bitrate=2000000, fd=True) as bus, \
        open(csv_filename, mode='x', newline='') as file_completely_full:

    writer_completely_full = csv.writer(file_completely_full)

    # Schreibe die Header-Zeile in die CSV-Datei
    headers = ["Timestamp", "ID", "DLC", "Data"]
    writer_completely_full.writerow(headers)

    # Flush + fsync die Header-Zeile auf die Festplatte
    file_completely_full.flush()
    os.fsync(file_completely_full.fileno())

    print(f"Starte CAN-Datenaufzeichnung nach {csv_filename} ...")

    sound_played = False  # Flag, um sicherzustellen, dass der Ton oder die LED nur einmal aktiviert wird

    try:
        while True:
            # Aktuelle Zeit berechnen
            current_time = time.time() - start_time

            # Auf Raspberry Pi: Lasse die LED aufleuchten; auf Windows: Spiele den Ton ab
            if 9.5 < current_time < 10.5 and not sound_played:
                if os.name == 'nt':
                    winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
                    print("Ton abgespielt bei 10 Sekunden")
                else:
                    set_led(True)
                    print("LED auf dem Raspberry Pi eingeschaltet bei 10 Sekunden")
                sound_played = True

            # Empfange eine Nachricht vom CAN-Bus
            msgrx = bus.recv(timeout=1.0)  # Timeout von 1 Sekunde

            if msgrx is not None:
                # Extrahiere relevante Daten aus der Nachricht
                timestamp = msgrx.timestamp
                can_id = f"{msgrx.arbitration_id:03X}"  # CAN-ID in hexadezimaler Darstellung
                dlc = msgrx.dlc
                data = msgrx.data.hex()  # Daten als Hex-String

                # Jede empfangene Nachricht wird ungefiltert geschrieben (keine
                # Dupletten-Erkennung, keine Ausdünnung) und sofort auf die
                # Festplatte durchgereicht: flush() gibt den Python-Puffer an
                # das Betriebssystem weiter, fsync() erzwingt zusätzlich das
                # physische Schreiben auf den Datenträger. Nur damit ist die
                # Aufzeichnung bis zur zuletzt empfangenen Nachricht auch dann
                # vollständig auf der SD-Karte, wenn der Log-Rechner (z. B. ein
                # Raspberry Pi) mitten in der Fahrt ohne sauberes Herunterfahren
                # ausfällt (Stromverlust, Absturz, Reset).
                writer_completely_full.writerow([timestamp, can_id, dlc, data])
                file_completely_full.flush()
                os.fsync(file_completely_full.fileno())
                print(f"Empfangen: ID={can_id}, DLC={dlc}, Daten={data}")

    except KeyboardInterrupt:
        # Wenn die Aufzeichnung durch Tastendruck abgebrochen wird
        print("Aufzeichnung unterbrochen.")

    # Schalte die LED auf dem Raspberry Pi aus, falls aktiviert
    if os.name != 'nt':
        set_led(False)

    print(f"Aufzeichnung beendet. Daten in {csv_filename} gespeichert.")
