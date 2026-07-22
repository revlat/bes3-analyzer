#!/usr/bin/env python3
"""Zeichnet BLE-Notifications des Bosch-eBike-Smart-Systems auf und schreibt
sie roh (Timestamp + Hex-Frame) in eine Textdatei. Read-only -- es werden
keine Nachrichten gesendet, keine Verbindung manipuliert.

Die eigentliche Verbindungslogik (Scannen, Verbinden, Notify-Characteristic
abonnieren) steckt in ble_connector.py -- dieses Skript ist nur ein duenner
CLI-Wrapper darum, der jede Notification als Zeile
"<sekunden_seit_start> <hex-bytes mit '-' getrennt>" anhaengt. Exakt das
Format, das bes3-decoder/decode_bes3.py (decode_ble_file/is_ble_log) und
bes3-log-plotter bereits einlesen koennen -- die Aufzeichnung ist also direkt
weiterverwendbar, ohne Zusatzarbeit.

Dieses Skript dekodiert selbst noch NICHTS -- reines Aufzeichnen. Fuer ein
kuenftiges Live-Dashboard (Dekodierung + Visualisierung waehrend der
Aufzeichnung) importiert man stattdessen ble_connector.stream_ble_notifications()
direkt und reicht die Bytes an decode_bes3.decode_ble_frame() weiter, siehe
Docstring von ble_connector.py.

Verbindungsabbruch: ble_connector.stream_ble_notifications() versucht bei
einem unerwarteten Abbruch (Bike ausser Reichweite/ausgeschaltet, BLE-Fehler)
automatisch erneut zu verbinden -- diese Aufzeichnung laeuft also ueber
Verbindungsaussetzer hinweg weiter, statt beim ersten Abbruch stehen zu
bleiben. Beendet wird ausschliesslich per Strg+C.

Nutzung:
    python3 bes3-ble-logger.py [--prefix ble] [--scan-timeout 10] [--reconnect-delay 3]
"""
import argparse
import asyncio
import os
import random
from datetime import datetime

from ble_connector import stream_ble_notifications


async def record(out_path, scan_timeout, reconnect_delay):
    gen = stream_ble_notifications(scan_timeout, reconnect_delay)
    f = None
    try:
        async for elapsed, data in gen:
            if f is None:
                # Datei erst bei der ERSTEN tatsaechlich empfangenen
                # Notification anlegen -- schlaegt das Verbinden/Finden des
                # Geraets fehl, bleibt keine leere Datei zurueck. mode='x'
                # statt 'w': schlaegt hart fehl statt eine bestehende Datei
                # stillschweigend zu ueberschreiben (siehe bes3-canfd-logger).
                f = open(out_path, mode='x')
                print(f"Aufzeichnung laeuft nach {out_path} -- Strg+C zum Beenden.")

            hex_str = "-".join(f"{b:02X}" for b in data)
            # Sofort durchreichen (flush + fsync) statt gepuffert zu lassen --
            # siehe „Ausfallsicherheit" in der README, gleiche Begruendung
            # wie beim CAN-FD-Logger.
            f.write(f"{elapsed:.6f} {hex_str}\n")
            f.flush()
            os.fsync(f.fileno())
            print(f"{elapsed:9.3f}s  {hex_str}")
    finally:
        await gen.aclose()
        if f is not None:
            f.close()

    if f is not None:
        print(f"Aufzeichnung beendet. Daten in {out_path} gespeichert.")


def main():
    parser = argparse.ArgumentParser(description="BLE-Notification-Logger (Bosch Smart System)")
    parser.add_argument('--prefix', type=str, default='ble',
                         help="Dateipraefix fuer die Ausgabedatei (Standard: ble)")
    parser.add_argument('--scan-timeout', type=float, default=10.0,
                         help="Sekunden, die pro Versuch nach dem Geraet gesucht wird (Standard: 10)")
    parser.add_argument('--reconnect-delay', type=float, default=3.0,
                         help="Wartezeit in Sekunden vor einem erneuten Verbindungsversuch "
                              "nach Abbruch/Fehler (Standard: 3)")
    args = parser.parse_args()

    # Dateiname wird bei jedem Start neu aus Datum+Uhrzeit+Zufallszahl gebildet,
    # damit aufeinanderfolgende Aufnahmen sich nie ueberschreiben -- siehe
    # bes3-canfd-logger fuer dieselbe Begruendung.
    timestamp_str = datetime.now().strftime("%Y%m%d-%H%M%S")
    random_suffix = random.randint(0, 9999)
    out_path = f"{args.prefix}_{timestamp_str}_{random_suffix:04d}_hex.txt"

    try:
        asyncio.run(record(out_path, args.scan_timeout, args.reconnect_delay))
    except KeyboardInterrupt:
        print("\nAufzeichnung unterbrochen.")


if __name__ == "__main__":
    main()
