#!/usr/bin/env python3
"""Eigenständiger Dekoder für die verifizierten BES3-Signale.

Aufbau der Frames:  [8B MAC][3B Freshness][1B][ Tag-Value-Payload ]
Payload-Eintrag:    [4B Entry-ID][Feld]

WICHTIG: Die meisten Felder sind KEINE festen Little-Endian-Integer, sondern
echte Protobuf-Wire-Format-Werte: `0x08` ist der Tag-Byte fuer "Feld 1,
Wire-Type 0 (Varint)", gefolgt von einem Varint (7 Nutzbits/Byte, MSB =
Fortsetzungsbit). Siehe die Haupt-README (../README.md) fuer Details und
Herleitung jeder Skala.

CLI-Nutzung (Textzusammenfassung einer aufgezeichneten Datei):
    python3 decode_bes3.py testfahrt3_completely_full.csv

Als Bibliothek: decode_file(path) fuer ganze Aufnahmen, decode_frame(data)
fuer eine einzelne (auch live empfangene) CAN-FD-Nachricht. Siehe README.md
in diesem Ordner fuer beide Nutzungsarten mit Beispielen.
"""
import csv
import re
import sys


def _read_varint(b, i=0):
    """Liest einen Protobuf-Varint ab Position i. Gibt (wert, naechste_position) zurueck."""
    result = 0
    shift = 0
    while i < len(b):
        byte = b[i]
        result |= (byte & 0x7F) << shift
        i += 1
        if not (byte & 0x80):
            return result, i
        shift += 7
    return None, i


def _varint_after_marker(b):
    """Erwartet b[0] == 0x08 (Protobuf-Tag Feld 1/Varint), liest den Varint danach."""
    if len(b) < 2 or b[0] != 0x08:
        return None
    val, _ = _read_varint(b, 1)
    return val


# Entry-ID (als Hex-String) -> Dekodierfunktion auf die Bytes NACH dem Tag

def _speed(b):    # 001C982D "DISPLAYED_BIKE_SPEED": Varint/100 -> km/h
    v = _varint_after_marker(b)
    return v / 100 if v is not None else None

def _current(b):  # 000061A8: KEIN 08-Marker, rohe 64-bit BE Bytes direkt (anderes Schema als
    # die Address-Registry-Felder, s. Haupt-README). Gesamtstrom der Antriebseinheit, nicht nur Motor.
    return int.from_bytes(b[0:8], "big") * 5.684341886e-14 if len(b) >= 8 else None

def _soc(b):      # 00108088 "STATE_OF_CHARGE": Varint DIREKT -> %
    return _varint_after_marker(b)

def _remaining_energy_wh(b):  # 00148091 "REMAINING_ENERGY_FOR_RIDER": Varint/10 -> Wh
    v = _varint_after_marker(b)
    return v / 10 if v is not None else None

def _torque(b):   # 00149815 "MOTOR_TORQUE": Varint/10 -> Nm
    v = _varint_after_marker(b)
    return v / 10 if v is not None else None

def _fet_temp(b):   # 001480d2 "PRESENT_FET_TEMPERATURE": Varint/10 -> Grad C direkt
    v = _varint_after_marker(b)
    return v / 10 if v is not None else None

def _pcb_temp(b):   # 00149884 "PRESENT_PCB_TEMPERATURE": Varint/10 -> Grad C direkt
    v = _varint_after_marker(b)
    return v / 10 if v is not None else None

def _pack_temp(b):  # 0014808b "PRESENT_PACK_TEMPERATURE": Varint/10 -> Grad C direkt
    v = _varint_after_marker(b)
    return v / 10 if v is not None else None

def _rider_cadence(b):  # 0010985a "RIDER_CADENCE": Varint direkt -> U/min
    return _varint_after_marker(b)

def _motor_cadence(b):  # 0010985c "MOTOR_CADENCE": Varint direkt -> U/min
    return _varint_after_marker(b)

def _motor_power(b):    # 0014985d "MOTOR_POWER": Varint direkt -> W
    return _varint_after_marker(b)

def _rider_power(b):    # 0014985b "RIDER_POWER": Varint direkt -> W
    return _varint_after_marker(b)

def _battery_voltage(b):  # 0018808c "PRESENT_BATTERY_VOLTAGE": Varint/1000 -> V
    v = _varint_after_marker(b)
    return v / 1000 if v is not None else None

def _calories(b):  # 0014a251 "CALORIES_CONSUMED": monotoner Zaehler,
    # Skala unkalibriert (evtl. roh=kcal direkt, keine unabhaengige Referenz vorhanden)
    return _varint_after_marker(b)

def _bike_speed_raw(b):  # 001C9808 "BIKE_SPEED": Varint/100 -> km/h. Ungerundeter Rohwert,
    # anders als DISPLAYED_BIKE_SPEED (das fuer die Anzeige gerundet/gefiltert wird) --
    # Werteverlauf korreliert eng mit DISPLAYED_BIKE_SPEED, nur ohne dessen Glaettung.
    v = _varint_after_marker(b)
    return v / 100 if v is not None else None

def _rider_torque(b):  # 00149814 "RIDER_TORQUE": Varint/10 -> Nm. Pedal-/Fahrer-Drehmoment,
    # analog zu MOTOR_TORQUE (gleiche Skala, plausibler Wertebereich, korreliert mit MOTOR_TORQUE).
    v = _varint_after_marker(b)
    return v / 10 if v is not None else None

def _remaining_energy_total_wh(b):  # 00148092 "REMAINING_ENERGY": Varint/10 -> Wh.
    # Ohne Fahrer-Normierung (s. verbleibende_energie_wh), aehnlicher Wertebereich.
    v = _varint_after_marker(b)
    return v / 10 if v is not None else None

def _motor_support_active(b):  # 00109883 "MOTOR_SUPPORT_ACTIVE": Varint DIREKT -> Bool (0/1)
    v = _varint_after_marker(b)
    return bool(v) if v is not None else None

def _continuous_pack_power(b):  # 001480D5 "CONTINUOUS_PACK_POWER": Varint DIREKT, keine
    # Skalierung -> W (Dauerleistung des Akku-Packs).
    return _varint_after_marker(b)

def _road_slope(b):  # 0010981D "ROAD_SLOPE": Varint DIREKT, keine Skalierung. Einheit
    # vermutlich % Steigung; in den vorhandenen Aufnahmen nur positive Werte (1-26)
    # beobachtet, Vorzeichen-Verhalten bei Gefaelle nicht verifiziert.
    return _varint_after_marker(b)

def _delivered_energy_lifetime_wh(b):  # 0014809C "DELIVERED_WH_OVER_LIFETIME": Varint DIREKT,
    # keine Skalierung -> Wh. Monotoner Lifetime-Zaehler (in Aufnahmen steigend beobachtet,
    # z. B. 4441 -> 4443 -> 4446 innerhalb einer Fahrt).
    return _varint_after_marker(b)

def _reachable_range(b):  # 9857 "REACHABLE_RANGE": NUR das untere Tag-Wort
    # ist konstant -- das obere Wort (0020/0024/0028/...) kodiert live die Byte-Laenge des
    # gepackten Varint-Arrays danach und wechselt automatisch, sobald ein Wert die 128-Schwelle
    # ueber-/unterschreitet (je 4 Byte mehr Payload = ein Wert braucht ein Byte mehr). Deshalb wird
    # dieses Signal ueber das 2-Byte-Suffix gematcht statt ueber den vollen 4-Byte-Tag.
    # Format: 0x0a <Laenge> <gepackte Varints>, exakt 4 Werte = Reichweite je Fahrmodus 1-4
    # (ECO, TOUR+, eMTB, TURBO -- Modus 0/OFF hat keine definierte Reichweite), km, keine Skalierung.
    # Faellt im Gleichschritt mit dem Ladezustand ueber eine Fahrt, absteigend sortiert.
    if len(b) < 2 or b[0] != 0x0A:
        return None
    length = b[1]
    payload = b[2:2 + length]
    vals = []
    i = 0
    while i < len(payload):
        v, i = _read_varint(payload, i)
        if v is None:
            return None
        vals.append(v)
    return vals if len(vals) == 4 else None

def _duration_without_stops_s(b):  # a243 "DURATION_WITHOUT_STOPS_OF_ACTIVITY": Varint DIREKT,
    # keine Skalierung -> Sekunden seit Aktivitaetsbeginn ohne Stopp (Wert steigt ueber eine
    # Aufnahme sauber von 1 auf ca. Fahrtdauer in Sekunden). Tritt sowohl unter Praefix 0010 als
    # auch 0014 auf (Praefix waechst mit der Wertgroesse, wie bei REACHABLE_RANGE) -- deshalb
    # Match nur ueber das 2-Byte-Suffix.
    return _varint_after_marker(b)

def _average_speed_kmh(b):  # 0014A246 "AVERAGE_SPEED": Varint/100 -> km/h, Durchschnitt der
    # laufenden Aktivitaet (Wertebereich liegt plausibel unterhalb der max. Momentangeschwindigkeit).
    v = _varint_after_marker(b)
    return v / 100 if v is not None else None

def _average_rider_power_w(b):  # a24a "AVERAGE_RIDER_POWER": Varint DIREKT, keine Skalierung
    # -> W. Tritt wie DURATION_WITHOUT_STOPS unter mehreren Praefixen auf, deshalb Suffix-Match.
    return _varint_after_marker(b)

def _rider_energy_share_percent(b):  # 0010A254 "RIDER_ENERGY_SHARE": Varint DIREKT, keine
    # Skalierung -> % Fahreranteil an der Antriebsleistung (Wertebereich 0-100 passt).
    return _varint_after_marker(b)

def _ambient_brightness_lux(b):  # a141 "AMBIENT_BRIGHTNESS": Varint/1000 -> Lux. Tritt unter
    # mehreren Praefixen auf (0018/001C, waechst mit der Wertgroesse), deshalb Suffix-Match.
    v = _varint_after_marker(b)
    return v / 1000 if v is not None else None

def _mobileapp_altitude_m(b):  # 0014C085 "ALTITUDE": Varint DIREKT (signed), keine Skalierung
    # -> Meter. Domaene/CAN-ID teilt sich diese Adresse mit anderen offensichtlich vom
    # gekoppelten Smartphone stammenden Aktivitaets-Werten (Durchschnittsgeschwindigkeit,
    # Trittfrequenz, Herzfrequenz) -- vermutlich also eine Handy-Hoehenschaetzung, kein Sensor
    # der Antriebseinheit. Trotz sauberer Dekodierung (Marker/Vorzeichen passen) mit Vorsicht
    # behandeln: die beobachteten Werte (356-424 m in einer Aufnahme) passten beim Abgleich mit
    # der tatsaechlichen Fahrtstrecke NICHT zur realen Hoehe -- Ursache ungeklaert.
    return _varint_after_marker(b)

# Fallback-Namen (Default-Config); die echte Zuordnung ist bike-spezifisch und
# wird zur Laufzeit aus der Modus-Liste in ID 401 gelesen (extract_mode_list).
_MODI_DEFAULT = {0: "OFF", 1: "ECO", 2: "TOUR+", 3: "eMTB", 4: "TURBO"}

def _mode(b):     # 00109809 "ASSIST_MODE": Varint direkt = Fahrmodus-Index (Position in Liste)
    return _varint_after_marker(b)

def _odo(b):      # 00189818 "ODOMETER": Varint direkt in METERN. roh/1000 -> km.
    v = _varint_after_marker(b)
    return v / 1000 if v is not None else None

SIGNALS = {
    "geschwindigkeit_kmh":     ("001C982D", _speed),
    "antriebsstrom_a":         ("000061A8", _current),  # Gesamtstrom Antriebseinheit, nicht nur Motor
    "ladezustand_prozent":     ("00108088", _soc),
    "verbleibende_energie_wh": ("00148091", _remaining_energy_wh),
    "motor_drehmoment_nm":     ("00149815", _torque),
    "fahrmodus":               ("00109809", _mode),
    "kilometerstand_km":       ("00189818", _odo),
    "fet_temperatur_c":        ("001480d2", _fet_temp),
    "pcb_temperatur_c":        ("00149884", _pcb_temp),
    "pack_temperatur_c":       ("0014808b", _pack_temp),
    "trittfrequenz_rpm":       ("0010985a", _rider_cadence),
    "motor_trittfrequenz_rpm": ("0010985c", _motor_cadence),
    "motor_leistung_w":        ("0014985d", _motor_power),
    "fahrer_leistung_w":       ("0014985b", _rider_power),
    "akku_spannung_v":         ("0018808c", _battery_voltage),
    "kalorien_ROHWERT":        ("0014a251", _calories),  # Skala unkalibriert
    "geschwindigkeit_roh_kmh": ("001C9808", _bike_speed_raw),
    "fahrer_drehmoment_nm":    ("00149814", _rider_torque),
    "verbleibende_energie_gesamt_wh": ("00148092", _remaining_energy_total_wh),
    "motorunterstuetzung_aktiv": ("00109883", _motor_support_active),
    "akku_pack_dauerleistung_w": ("001480D5", _continuous_pack_power),
    "strassenneigung_ROHWERT": ("0010981D", _road_slope),  # Einheit/Vorzeichen unverifiziert
    "abgegebene_energie_lifetime_wh": ("0014809C", _delivered_energy_lifetime_wh),
    "reichweite_km_je_modus": ("9857", _reachable_range),  # nur 2-Byte-Suffix, s. Kommentar bei der Funktion
    "aktivitaet_dauer_ohne_stopp_s": ("a243", _duration_without_stops_s),  # nur 2-Byte-Suffix
    "aktivitaet_durchschnittsgeschwindigkeit_kmh": ("0014A246", _average_speed_kmh),
    "aktivitaet_durchschnittsleistung_fahrer_w": ("a24a", _average_rider_power_w),  # nur 2-Byte-Suffix
    "fahreranteil_leistung_prozent": ("0010A254", _rider_energy_share_percent),
    "umgebungshelligkeit_lux": ("a141", _ambient_brightness_lux),  # nur 2-Byte-Suffix
    "hoehe_smartphone_m": ("0014C085", _mobileapp_altitude_m),  # s. Kommentar: vom Handy, nicht vom Bike
}


def extract_mode_list(path):
    """Liest die konfigurierbare Fahrmodus-Liste aus ID 401 (Reihenfolge = Index).

    Format je Eintrag: 0x0a <laenge> <ascii-name>. Gibt {index: name} zurueck,
    sonst die Default-Namen.
    """
    with open(path) as f:
        r = csv.reader(f)
        next(r)
        for row in r:
            if len(row) < 4 or row[1] != "401":
                continue
            b = bytes.fromhex(row[3])
            if b"OFF" not in b or b"TURBO" not in b:
                continue
            start = b.find(b"OFF") - 2  # Beginn des ersten 0a-Eintrags
            names, i = [], start
            while i + 2 <= len(b) and b[i] == 0x0A:
                ln = b[i + 1]
                name = b[i + 2:i + 2 + ln]
                if not name or not all(32 <= c < 127 for c in name):
                    break
                names.append(name.decode("ascii"))
                i += 2 + ln
            if names:
                return {idx: n for idx, n in enumerate(names)}
    return dict(_MODI_DEFAULT)


# Bekannte Bosch-Bauteil-Typcodes, siehe "Klartext-Kennungen" in der Haupt-README.
KNOWN_COMPONENT_CODES = {
    "BDU3740": "Antriebseinheit/Motor (Performance Line CX)",
    "BBP3770": "Akku (Battery Pack)",
    "BCM3100": "ConnectModule",
    "BHU3600": "Bedien-/Steuereinheit (Display)",
}

_ASCII_RUN = re.compile(rb"[\x20-\x7e]{6,}")

# Bekannte CAN-ID-Domaenen fuer Klartext-Strings, siehe "Klartext-Kennungen"
# bzw. "Ladegeraet-/Akku-Domaene (6xx)" in der Haupt-README. Beobachtung,
# keine offizielle Spezifikation -- nur als Einordnungshilfe gedacht.
CAN_ID_HINTS = {
    "613": "Ladegeraet-Seriennummer/EN-Nummer",
    "603": "Akku-Seriennummer",
    "401": "Konfig-/Parameter-Tabelle",
    "3C2": "Konfig-/Parameter-Tabelle",
    "409": "Konfig-/Parameter-Tabelle",
}


def extract_ascii_strings(path, min_count=3):
    """Findet wiederkehrende druckbare ASCII-Teilstrings (>=6 Zeichen) ueber
    alle Frames einer Aufnahme -- Bauteil-Typcodes, Modus-Namen, Region/
    Geschwindigkeitsbegrenzung, Parameter-Codes, Teile-/Seriennummern etc.,
    siehe "Klartext-Kennungen" in der Haupt-README. Generischer strings(1)-
    artiger Scan, keine Interpretation der Bedeutung -- ABER: jeder String
    wird mit der Menge der CAN-IDs zurueckgegeben, in denen er auftaucht.
    Ein String klebt i. d. R. an einem festen, stabilen CAN-ID-Set (z. B.
    taucht ein Bluetooth-Geraetename nur in einer bestimmten ID auf, ein
    anderer String nur in zwei-drei anderen) -- das ist der Anhaltspunkt,
    um ohne weitere Dekodierung zu erkennen "das ist vermutlich X" (siehe
    describe_can_ids() fuer den Abgleich gegen CAN_ID_HINTS).

    Bei Antriebsstrang-Frames werden die ersten 12 Byte (MAC+Freshness-
    Header) uebersprungen, damit deren Zufalls-Bytes nicht als Text
    missinterpretiert werden; die Ladegeraet-/Akku-Domaene (CAN-ID beginnt
    mit "6") hat keinen solchen Header und wird komplett gescannt.

    Um trotzdem verbliebenes Zufallsrauschen auszufiltern: nur Strings, die
    in mindestens `min_count` VERSCHIEDENEN Frames auftauchen, werden
    zurueckgegeben. Echter, wiederholt gesendeter Text (Seriennummer,
    Bauteilcode, Parameter-Label, ...) erfuellt das i. d. R. muehelos --
    ein zufaelliger Treffer im Header-Rauschen praktisch nie zweimal
    identisch.

    Gibt {string: {"count": anzahl_frames, "can_ids": {id, ...}}} zurueck."""
    found = {}
    with open(path) as f:
        r = csv.reader(f)
        next(r)
        for row in r:
            if len(row) < 4:
                continue
            can_id = row[1]
            data = bytes.fromhex(row[3])
            body = data if can_id.startswith("6") else data[12:]
            for m in _ASCII_RUN.finditer(body):
                s = m.group().decode("ascii")
                entry = found.setdefault(s, {"count": 0, "can_ids": set()})
                entry["count"] += 1
                entry["can_ids"].add(can_id)
    return {s: v for s, v in found.items() if v["count"] >= min_count}


def describe_can_ids(can_ids):
    """Ordnet eine Menge CAN-IDs (aus extract_ascii_strings) einer bekannten
    Domaene zu, wenn eine der IDs in CAN_ID_HINTS bekannt ist -- zeigt sonst
    (bzw. zusaetzlich) die rohen IDs, damit auch unbekannte, aber stabile
    ID-Kombinationen sichtbar bleiben. Nur eine Einordnungshilfe, keine
    Garantie fuer die tatsaechliche Bedeutung."""
    ids_str = ",".join(sorted(can_ids))
    hints = sorted({CAN_ID_HINTS[c] for c in can_ids if c in CAN_ID_HINTS})
    if hints:
        return f"{ids_str} [{'/'.join(hints)}]"
    return ids_str


def decode_frame(data):
    """Dekodiert EINE rohe CAN-FD-Frame-Payload (z. B. `msg.data` von python-can)
    und gibt {signalname: wert} fuer alle in diesem einen Frame gefundenen
    Signale zurueck (leeres Dict, wenn keins passt). Zustandslos -- jeder
    Aufruf ist unabhaengig von vorherigen Frames, daher direkt fuer
    Live-Dekodierung Frame-fuer-Frame verwendbar, nicht nur fuer Dateien."""
    if len(data) < 12:
        return {}
    payload = data[12:]
    out = {}
    for name, (tag, fn) in SIGNALS.items():
        tag_bytes = bytes.fromhex(tag)
        i = payload.find(tag_bytes)
        if i < 0:
            continue
        val = fn(payload[i + len(tag_bytes):])
        if val is not None:
            out[name] = val
    return out


def decode_file(path):
    out = {name: [] for name in SIGNALS}
    with open(path) as f:
        r = csv.reader(f)
        next(r)
        for row in r:
            if len(row) < 4:
                continue
            ts = float(row[0])
            data = bytes.fromhex(row[3])
            for name, val in decode_frame(data).items():
                out[name].append((ts, val))
    return out


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    for path in sys.argv[1:]:
        res = decode_file(path)
        modes = extract_mode_list(path)  # {index: name} aus dieser Aufnahme
        print(f"\n=== {path} ===")
        print(f"  Fahrmodus-Liste: {', '.join(f'{i}={n}' for i, n in modes.items())}")
        for name, series in res.items():
            if not series:
                print(f"  {name:22}: (keine Daten)")
                continue
            vals = [v for _, v in series]
            if name == "fahrmodus":
                # Index ueber die Liste dieser Aufnahme in Namen aufloesen
                seq = []
                for v in vals:
                    lbl = f"{v}({modes.get(v, '?')})"
                    if not seq or seq[-1] != lbl:
                        seq.append(lbl)
                print(f"  {name:22}: n={len(series):6}  Verlauf: {' -> '.join(seq)}")
            elif name == "reichweite_km_je_modus":
                # vals sind 4er-Listen (ein Wert je Fahrmodus), kein einzelner Skalar
                print(f"  {name:22}: n={len(series):6}  erst={vals[0]}  letzt={vals[-1]}")
            else:
                print(f"  {name:22}: n={len(series):6}  "
                      f"min={min(vals):8.2f}  max={max(vals):8.2f}  "
                      f"erst={vals[0]:8.2f}  letzt={vals[-1]:8.2f}")

        strings = extract_ascii_strings(path)
        print("  Bauteil-Typcodes:")
        for code, label in KNOWN_COMPONENT_CODES.items():
            if code in strings:
                print(f"    {code}  {label}")
        rest = {s: v for s, v in strings.items() if s not in KNOWN_COMPONENT_CODES}
        if rest:
            print("  Weitere wiederkehrende Text-Strings (>=3x, mit CAN-ID(s)):")
            for s, v in sorted(rest.items(), key=lambda kv: -kv[1]["count"]):
                print(f"    {v['count']:5}x  {s:30}  [{describe_can_ids(v['can_ids'])}]")


if __name__ == "__main__":
    main()
