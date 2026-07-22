#!/usr/bin/env python3
"""Wiederverwendbare BLE-Verbindungslogik fuer das Bosch-eBike-Smart-System.

Kapselt Scannen, Verbinden und Notify-Abonnement in einem Async-Generator
(`stream_ble_notifications()`), damit sowohl `bes3-ble-logger.py` (schreibt
die rohen Notifications in eine Datei) als auch ein kuenftiges Live-Dashboard
(dekodiert/visualisiert live) dieselbe Verbindungslogik nutzen koennen, ohne
sie zu duplizieren -- eine einzige Quelle der Wahrheit fuer Geraetename und
UUIDs, analog zum SIGNALS-Prinzip in bes3-decoder/decode_bes3.py.

Liefert ausschliesslich rohe Bytes -- KEINE Dekodierlogik hier. Zum Dekodieren
siehe decode_ble_frame() in ../bes3-decoder/decode_bes3.py:

    import ble_connector as c
    import decode_bes3 as d

    async for ts, data in c.stream_ble_notifications():
        for name, wert in d.decode_ble_frame(data).items():
            print(f"{ts:.3f}s  {name}: {wert}")

Read-only -- es wird ausschliesslich auf Notifications gehoert (start_notify),
nie geschrieben/gesteuert.

Verbindungsabbruch: BLE-Notify-Abos haben kein Ablaufdatum -- einmal
abonniert, bleiben sie bestehen, solange die Verbindung lebt. Bricht die
Verbindung unerwartet ab (Bike ausser Reichweite/ausgeschaltet, Akku leer,
Stoerung), versucht stream_ble_notifications() automatisch erneut zu
verbinden (Scan + Connect + Notify neu), statt einfach stillschweigend zu
verstummen. Nur ein expliziter Abbruch durch den Aufrufer (Strg+C,
`gen.aclose()`) beendet den Generator wirklich -- siehe Docstring der
Funktion fuer Details zur Unterscheidung.
"""
import asyncio
import time

from bleak import BleakClient, BleakScanner

# Per nRF Connect ermittelt (siehe README).
DEVICE_NAME = "smart system eBike"
SERVICE_UUID = "00000010-eaa2-11e9-81b4-2a2ae2dbcce4"
CHAR_UUID_NOTIFY = "00000011-eaa2-11e9-81b4-2a2ae2dbcce4"

_DISCONNECTED = object()  # interner Sentinel-Wert fuer die Queue, kein Notification-Inhalt


async def stream_ble_notifications(scan_timeout=10.0, reconnect_delay=3.0):
    """Verbindet sich mit DEVICE_NAME und liefert jede eintreffende
    Notification auf CHAR_UUID_NOTIFY als (sekunden_seit_erstem_start, bytes).

    Async-Generator -- Verbrauch per `async for ts, data in
    stream_ble_notifications(): ...`. Laeuft endlos.

    Verbindungsabbruch (Bike ausser Reichweite/ausgeschaltet, Scan-Timeout,
    sonstiger BLE-Fehler): wird ueber Konsolen-Ausgabe sichtbar gemacht, nach
    `reconnect_delay` Sekunden automatisch ein neuer Verbindungsversuch
    gestartet -- der Generator laeuft also weiter, ohne dass der Aufrufer
    selbst neu verbinden muss. `ts` bleibt dabei auf denselben Nullpunkt
    bezogen (Zeit seit dem allerersten Verbindungsversuch), damit eine
    Aufzeichnung ueber mehrere Verbindungsabbrueche hinweg eine durchgehend
    steigende Zeitachse behaelt statt bei jedem Reconnect auf 0 zu
    zurueckspringen (eine Luecke waehrend der Trennung ist dabei normal und
    korrekt, kein Fehler).

    Nur ein expliziter Abbruch durch den AUFRUFER beendet den Generator
    wirklich: Strg+C im Aufrufer oder `gen.aclose()`. Das wird intern ueber
    GeneratorExit signalisiert, das anders als normale BLE-Fehler NICHT
    abgefangen und retried wird (siehe `except Exception` unten -- das faengt
    bewusst KEIN BaseException wie GeneratorExit/KeyboardInterrupt ab) --
    stattdessen laufen die finally-Bloecke (Notify abmelden, Verbindung
    schliessen) und der Generator endet endgueltig.
    """
    start_time = time.time()

    while True:
        try:
            print(f"Suche Geraet '{DEVICE_NAME}' ...")
            device = await BleakScanner.find_device_by_name(DEVICE_NAME, timeout=scan_timeout)
            if device is None:
                print(f"Geraet '{DEVICE_NAME}' nicht gefunden (Timeout {scan_timeout}s) -- "
                      f"naechster Versuch in {reconnect_delay:.0f}s.")
                await asyncio.sleep(reconnect_delay)
                continue

            queue = asyncio.Queue()

            def handle_notification(_sender, data):
                queue.put_nowait((time.time() - start_time, bytes(data)))

            def handle_disconnect(_client):
                queue.put_nowait(_DISCONNECTED)

            async with BleakClient(device, disconnected_callback=handle_disconnect) as client:
                print(f"Verbunden mit {device.address}.")
                if client.services.get_service(SERVICE_UUID) is None:
                    print(f"WARNUNG: Service {SERVICE_UUID} nicht in den GATT-Services "
                          "dieses Geraets gefunden -- versuche trotzdem, die "
                          "Notify-Characteristic zu abonnieren.")

                await client.start_notify(CHAR_UUID_NOTIFY, handle_notification)
                try:
                    while True:
                        item = await queue.get()
                        if item is _DISCONNECTED:
                            print("Verbindung verloren -- versuche erneut zu verbinden ...")
                            break
                        yield item
                finally:
                    if client.is_connected:
                        await client.stop_notify(CHAR_UUID_NOTIFY)

        except Exception as exc:
            # Bewusst Exception, nicht BaseException: GeneratorExit (Strg+C
            # beim Aufrufer / gen.aclose()) und KeyboardInterrupt sollen HIER
            # NICHT abgefangen werden, sonst wuerde ein gewollter Abbruch
            # faelschlich als Verbindungsfehler behandelt und endlos retried.
            print(f"Fehler in der BLE-Verbindung ({exc}) -- naechster Versuch "
                  f"in {reconnect_delay:.0f}s.")

        await asyncio.sleep(reconnect_delay)
