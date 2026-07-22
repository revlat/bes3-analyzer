#!/usr/bin/env python3
"""Live-Cockpit (Web-Dashboard) fuer BLE-Notifications des Bosch-eBike-
Smart-Systems: zeigt konfigurierbare Gauges/VU-Meter/LED-Baelken/Zahlen/
Badges, die sich SOFORT bei jeder eintreffenden, dekodierten Notification
aktualisieren -- kein Zeitintervall, kein Polling, echtes ereignisgetriebenes
Update (wichtig z. B. fuer den Tacho, der sonst ruckelig wirkt). Zusaetzlich
zwei Uhrzeit-Kacheln (Text/analoge Uhr), die unabhaengig vom Bike einmal pro
Sekunde ueber die Systemuhr laufen.

Nutzt ausschliesslich vorhandene Bausteine weiter, keine eigene
Verbindungs-/Dekodierlogik:
  - ble_connector.stream_ble_notifications() (../bes3-ble-logger) fuer
    Scannen/Verbinden/Notify-Abo inkl. automatischem Reconnect.
  - decode_bes3.decode_ble_frame() (../bes3-decoder) fuer die Dekodierung
    (dieselbe SIGNALS-Tabelle wie CAN-FD).

Anzeige-Konfiguration in config.yaml (Standard: Datei neben diesem Skript) --
siehe README.md fuer das Format und Beispiele.

Nutzung:
    python3 bes3-ble-cockpit.py [--config config.yaml] [--port 8080]
        [--scan-timeout 10] [--reconnect-delay 3]

Gedacht fuer eine einzelne lokale Browser-Session (ein Nutzer, ein Bike) --
kein Mehrbenutzer-/Mehr-Bike-Betrieb.
"""
import argparse
import asyncio
import os
import sys
import time
from datetime import datetime

import yaml
from nicegui import app, ui

_NO_DATA_TIMEOUT_S = 10.0  # ab hier "keine Daten"-Overlay einblenden

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, "..", "bes3-decoder"))
sys.path.insert(0, os.path.join(BASE, "..", "bes3-ble-logger"))
import decode_bes3 as d
import ble_connector as c

_GAUGE_COLORS = ["#ff4d4f", "#faad14", "#52c41a"]  # rot, gelb, gruen


def load_config(path):
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if not cfg or "gauges" not in cfg:
        raise ValueError(f"Config {path} enthaelt keinen 'gauges'-Abschnitt.")
    return cfg


def _color_stops(vmin, vmax, thresholds, invert=False):
    """Baut die ECharts-axisLine-Farbzonen aus absoluten Schwellwerten, z. B.
    thresholds=[20, 50] bei min=0/max=100 -> rot bis 20%, gelb bis 50%, gruen
    darueber. Ohne thresholds: einheitliche Farbe ueber den ganzen Bereich.

    invert=True dreht die Farbreihenfolge um (gruen -> gelb -> rot statt rot
    -> gelb -> gruen) -- fuer Signale, bei denen ein HOHER Wert schlecht ist
    (z. B. Temperaturen), waehrend rot->gruen bei aufsteigenden Werten (wie
    beim Akku, wo hoch = gut ist) die Standardrichtung bleibt."""
    if not thresholds:
        return [[1, "#1976d2"]]
    colors = list(reversed(_GAUGE_COLORS)) if invert else _GAUGE_COLORS
    span = vmax - vmin
    stops = []
    for i, bound in enumerate([*thresholds, vmax]):
        frac = max(0.0, min(1.0, (bound - vmin) / span))
        stops.append([frac, colors[min(i, len(colors) - 1)]])
    return stops


def _zone_color(value, vmin, vmax, thresholds, invert=False):
    """Wie _color_stops, aber fuer EINEN absoluten Wert statt einer ganzen
    ECharts-Farbverlaufsliste -- welche Zone (rot/gelb/gruen, bzw. bei
    invert=True gruen/gelb/rot) deckt `value` ab. Genutzt vom LED-Balken, wo
    jedes einzelne Segment seine eigene Farbe braucht statt eines
    kontinuierlichen Farbverlaufs."""
    if not thresholds:
        return "#1976d2"
    colors = list(reversed(_GAUGE_COLORS)) if invert else _GAUGE_COLORS
    for i, bound in enumerate([*thresholds, vmax]):
        if value <= bound:
            return colors[min(i, len(colors) - 1)]
    return colors[-1]


_LED_BAR_OFF_COLOR = "#e0e0e0"
_LED_BAR_DEFAULT_SEGMENTS = 20


def build_led_bar_option(cfg):
    """Baut eine ECharts-Balken-Option im Stil eines vertikalen LED-VU-Meters
    (Aussteuerungsanzeige mit einzelnen, diskreten Segmenten statt einer
    stufenlosen Nadel) -- eine Spalte aus `segments` einzelnen Bloecken, die
    von unten nach oben "leuchten" (volle Zonenfarbe), waehrend unbeleuchtete
    Segmente grau/gedimmt bleiben. Hochkant (schmal, hoch) statt der
    Kreis-Optik von build_gauge_option/build_vu_meter_option.

    Die tatsaechliche "Wieviele Segmente leuchten"-Logik + Farbzuweisung
    passiert in Tile.update() -- hier nur die initiale (alles aus) Option."""
    n = cfg.get("segments", _LED_BAR_DEFAULT_SEGMENTS)
    categories = list(range(n))  # 0 = unterstes Segment
    return {
        "grid": {"left": 2, "right": 2, "top": 2, "bottom": 2, "containLabel": False},
        "xAxis": {"type": "value", "max": 1, "show": False},
        # inverse=True, damit Segment 0 (unterstes Segment) tatsaechlich UNTEN
        # landet -- ECharts' Standard-Reihenfolge fuer eine Kategorie-y-Achse
        # setzt Index 0 sonst oben hin (im Live-Test bestaetigt: inverse=True
        # leuchtete verkehrt herum, also OHNE inverse ist Index 0 unten).
        "yAxis": {"type": "category", "data": categories, "show": False},
        "series": [{
            "type": "bar",
            "data": [{"value": 1, "itemStyle": {"color": _LED_BAR_OFF_COLOR}} for _ in categories],
            "barCategoryGap": "18%",
            "barWidth": "82%",
            "silent": True,
            "animation": False,
        }],
    }


def build_clock_analog_option():
    """ECharts-Gauge-Trio (Stunde/Minute/Sekunde uebereinander auf demselben
    Ziffernblatt) -- ergibt optisch eine analoge Uhr. startAngle=90/
    endAngle=-270 heisst: 0 oben (12-Uhr-Position), im Uhrzeigersinn einmal
    komplett herum -- der uebliche ECharts-Trick fuer Uhren-Visualisierungen."""
    common = {"startAngle": 90, "endAngle": -270, "detail": {"show": False}, "anchor": {"show": False}}
    return {
        "series": [
            {  # Ziffernblatt + Stunden-Zeiger (0-12)
                **common,
                "type": "gauge",
                "min": 0, "max": 12, "splitNumber": 12,
                "axisLine": {"lineStyle": {"width": 3, "color": [[1, "#999"]]}},
                "axisTick": {"show": True, "splitNumber": 5, "length": 6, "lineStyle": {"color": "#999"}},
                "splitLine": {"length": 10, "lineStyle": {"color": "#666"}},
                "axisLabel": {"show": False},
                "pointer": {"width": 7, "length": "45%", "itemStyle": {"color": "#333"}},
                "data": [{"value": 0}],
            },
            {  # Minuten-Zeiger (0-60, unsichtbares Ziffernblatt)
                **common,
                "type": "gauge",
                "min": 0, "max": 60,
                "axisLine": {"show": False}, "axisTick": {"show": False},
                "splitLine": {"show": False}, "axisLabel": {"show": False},
                "pointer": {"width": 5, "length": "68%", "itemStyle": {"color": "#333"}},
                "data": [{"value": 0}],
            },
            {  # Sekunden-Zeiger (0-60, unsichtbares Ziffernblatt)
                **common,
                "type": "gauge",
                "min": 0, "max": 60,
                "axisLine": {"show": False}, "axisTick": {"show": False},
                "splitLine": {"show": False}, "axisLabel": {"show": False},
                "pointer": {"width": 2, "length": "78%", "itemStyle": {"color": "#d32f2f"}},
                "anchor": {"show": True, "size": 8, "itemStyle": {"color": "#d32f2f"}},
                "data": [{"value": 0}],
            },
        ],
    }


_RING_MODE_NAMES = ["ECO", "TOUR+", "eMTB", "TURBO"]  # Reihenfolge = Index 0-3 in reichweite_km_je_modus
_RING_COLORS = ["#52c41a", "#1976d2", "#faad14", "#ff4d4f"]
_RING_RADII = ["100%", "78%", "56%", "34%"]  # aussen -> innen, je ein Ring pro Fahrmodus


def build_ring_gauge_option(cfg):
    """Baut eine Ring-Gauge-Option (mehrere konzentrische Fortschrittsringe,
    wie bei Activity-Ringen) fuer reichweite_km_je_modus -- dieses Signal
    liefert einen 4er-Wert (Reichweite je Fahrmodus 1-4: ECO/TOUR+/eMTB/
    TURBO), kein einzelner Skalar wie bei den anderen Signalen. Jeder Ring
    ist eine eigene ECharts-Gauge-Serie ohne Nadel (progress-Ring statt
    Zeiger), nur mit unterschiedlichem Radius uebereinandergelegt."""
    vmax = cfg.get("max", 150)
    series = []
    for name, color, radius in zip(_RING_MODE_NAMES, _RING_COLORS, _RING_RADII):
        series.append({
            "type": "gauge",
            "startAngle": 90,
            "endAngle": -270,
            "min": 0,
            "max": vmax,
            "radius": radius,
            "pointer": {"show": False},
            "progress": {"show": True, "width": 10, "itemStyle": {"color": color}},
            "axisLine": {"lineStyle": {"width": 10, "color": [[1, "#eee"]]}},
            "axisTick": {"show": False},
            "splitLine": {"show": False},
            "axisLabel": {"show": False},
            "anchor": {"show": False},
            "title": {"show": False},
            "detail": {"show": False},
            "data": [{"value": 0, "name": name}],
        })
    return {"series": series}


def build_gauge_option(cfg):
    """Baut die initiale ECharts-Gauge-Option aus einem Signal-Config-Eintrag."""
    vmin = cfg.get("min", 0)
    vmax = cfg.get("max", 100)
    unit = cfg.get("unit", "")
    label = cfg.get("label", cfg["signal"])
    # ECharts zeichnet Skala/Titel/Wert selbst ins SVG -- das erbt KEINE
    # CSS-Textfarbe vom umgebenden HTML (anders als ui.label-Elemente), muss
    # also hier explizit gesetzt werden. gauge_text_color wird von build_app()
    # aus display.gauge_text_color in jede gauges-Config injiziert.
    text_color = cfg.get("gauge_text_color")
    return {
        "series": [{
            "type": "gauge",
            "min": vmin,
            "max": vmax,
            "startAngle": 210,
            "endAngle": -30,
            "axisLine": {"lineStyle": {"width": 14,
                                        "color": _color_stops(vmin, vmax, cfg.get("thresholds"),
                                                               cfg.get("invert_colors", False))}},
            "pointer": {"width": 4},
            "progress": {"show": False},
            "axisTick": {"show": True, "distance": -14, "length": 4},
            "splitLine": {"distance": -14, "length": 10},
            "axisLabel": {"distance": -20, "fontSize": 10, "color": text_color},
            "anchor": {"show": True, "size": 12, "showAbove": True},
            "title": {"fontSize": 13, "offsetCenter": [0, "75%"], "color": text_color},
            "detail": {
                "valueAnimation": True,
                "formatter": "{value}" + (f" {unit}" if unit else ""),
                "fontSize": 20,
                "offsetCenter": [0, "40%"],
                "color": text_color,
            },
            "data": [{"value": vmin, "name": label}],
        }],
    }


def build_vu_meter_option(cfg):
    """Baut eine ECharts-Gauge-Option im Stil eines klassischen analogen
    VU-Meters: flacher Bogen (120 Grad statt der 240 Grad bei build_gauge_option)
    mit dem Drehpunkt unterhalb des sichtbaren Bogens -- die typische
    "flache" Optik von Aussteuerungsanzeigen, statt einer Vollkreis-Uhr.
    Kraeftigere Nadel/Skala als bei build_gauge_option. Nutzt dieselbe
    Farbzonen-Logik (_color_stops) -- bei einem Akku bleibt rot=niedrig,
    gruen=hoch (VU-Meter-Optik uebernommen, nicht die Gefahrenzone-am-oberen-
    Ende-Semantik echter Aussteuerungsanzeigen)."""
    vmin = cfg.get("min", 0)
    vmax = cfg.get("max", 100)
    unit = cfg.get("unit", "")
    label = cfg.get("label", cfg["signal"])
    text_color = cfg.get("gauge_text_color")
    return {
        "series": [{
            "type": "gauge",
            "min": vmin,
            "max": vmax,
            "startAngle": 150,
            "endAngle": 30,
            "center": ["50%", "75%"],
            "radius": "100%",
            "axisLine": {"lineStyle": {"width": 16,
                                        "color": _color_stops(vmin, vmax, cfg.get("thresholds"),
                                                               cfg.get("invert_colors", False))}},
            "pointer": {"width": 6, "length": "65%", "itemStyle": {"color": "#333"}},
            "progress": {"show": False},
            "axisTick": {"show": True, "distance": -16, "length": 5},
            "splitLine": {"distance": -16, "length": 12, "lineStyle": {"width": 2}},
            "splitNumber": 5,
            "axisLabel": {"distance": -26, "fontSize": 10, "color": text_color},
            "anchor": {"show": True, "size": 10, "showAbove": True, "itemStyle": {"color": "#333"}},
            "title": {"fontSize": 13, "offsetCenter": [0, "15%"], "color": text_color},
            "detail": {
                "valueAnimation": True,
                "formatter": "{value}" + (f" {unit}" if unit else ""),
                "fontSize": 16,
                "offsetCenter": [0, "-20%"],
                "color": text_color,
            },
            "data": [{"value": vmin, "name": label}],
        }],
    }


class Tile:
    """Eine Anzeige-Kachel fuer genau ein Signal (Gauge/VU-Meter/LED-Balken/
    Zahl/Badge) ODER eine Uhrzeit-Anzeige (clock_text/clock_analog -- kein
    Signal, wird stattdessen jede Sekunde ueber einen ui.timer aktualisiert)."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.signal = cfg.get("signal")  # None bei clock_text/clock_analog
        self.display_type = cfg.get("type", "gauge")
        # Kein automatischer Fallback auf den Signalnamen -- ein rohes
        # "hoehe_smartphone_m" als Titel waere haesslicher als gar kein
        # Titel. Wer eine Beschriftung will, gibt sie explizit an.
        label = cfg.get("label", "")

        if self.display_type == "gauge":
            self.chart = ui.echart(build_gauge_option(cfg)).classes("w-56 h-48")
        elif self.display_type == "vu_meter":
            self.chart = ui.echart(build_vu_meter_option(cfg)).classes("w-56 h-36")
        elif self.display_type == "led_bar":
            n = cfg.get("segments", _LED_BAR_DEFAULT_SEGMENTS)
            vmin, vmax = cfg.get("min", 0), cfg.get("max", 100)
            thresholds = cfg.get("thresholds")
            # Farbe, die Segment i haben SOLL, wenn es leuchtet -- einmalig
            # vorberechnet, damit update() nicht bei jeder Notification neu
            # rechnen muss.
            invert = cfg.get("invert_colors", False)
            self.segment_colors = [
                _zone_color(vmin + (i + 0.5) / n * (vmax - vmin), vmin, vmax, thresholds, invert)
                for i in range(n)
            ]
            with ui.column().classes("items-center gap-1"):
                if label:
                    ui.label(label).classes("text-sm text-gray-500")
                self.chart = ui.echart(build_led_bar_option(cfg)).classes("w-16 h-64")
        elif self.display_type == "ring_gauge":
            # reichweite_km_je_modus liefert einen 4er-Wert (ein km-Wert je
            # Fahrmodus), keinen Skalar -- pro Ring eine eigene kleine
            # Text-Legende (Farbe + Modus + aktueller km-Wert), weil die
            # konkreten Zahlen hier die eigentliche Information sind, nicht
            # nur ein Anteil.
            with ui.column().classes("items-center gap-1"):
                if label:
                    ui.label(label).classes("text-sm text-gray-500")
                self.chart = ui.echart(build_ring_gauge_option(cfg)).classes("w-48 h-48")
                self.legend_labels = []
                for name, color in zip(_RING_MODE_NAMES, _RING_COLORS):
                    with ui.row().classes("items-center gap-1"):
                        ui.html(f'<span style="display:inline-block;width:10px;height:10px;'
                                f'border-radius:50%;background:{color}"></span>')
                        lbl = ui.label(f"{name}: -- km").classes("text-xs")
                        self.legend_labels.append(lbl)
        elif self.display_type == "number":
            with ui.column().classes("items-center"):
                if label:
                    ui.label(label).classes("text-sm text-gray-500")
                self.value_label = ui.label("--").classes("text-4xl font-bold")
        elif self.display_type == "badge":
            with ui.column().classes("items-center"):
                if label:
                    ui.label(label).classes("text-sm text-gray-500")
                self.value_label = ui.label("--").classes("text-3xl font-bold")
        elif self.display_type == "clock_text":
            # Kein Titel-Label -- eine Uhrzeit erklaert sich selbst.
            self.value_label = ui.label("--:--:--").classes("text-4xl font-bold font-mono")
            self._tick_clock_text()
            ui.timer(1.0, self._tick_clock_text)
        elif self.display_type == "clock_analog":
            # Kein Titel-Label -- eine Uhrzeit erklaert sich selbst.
            self.chart = ui.echart(build_clock_analog_option()).classes("w-40 h-40")
            self._tick_clock_analog()
            ui.timer(1.0, self._tick_clock_analog)
        else:
            raise ValueError(f"Unbekannter Anzeigetyp fuer Signal '{self.signal}': "
                              f"{self.display_type!r} (erlaubt: gauge, vu_meter, led_bar, "
                              f"ring_gauge, number, badge, clock_text, clock_analog)")

    def update(self, value):
        """Wird pro dekodierter BLE-Notification aufgerufen -- nicht
        zustaendig fuer clock_text/clock_analog, die laufen unabhaengig
        ueber ihren eigenen ui.timer (siehe _tick_clock_*)."""
        if self.display_type in ("gauge", "vu_meter"):
            v = round(value, 1) if isinstance(value, float) else value
            self.chart.options["series"][0]["data"][0]["value"] = v
            self.chart.update()
        elif self.display_type == "led_bar":
            vmin, vmax = self.cfg.get("min", 0), self.cfg.get("max", 100)
            n = len(self.segment_colors)
            frac = max(0.0, min(1.0, (value - vmin) / (vmax - vmin))) if vmax > vmin else 0.0
            lit = round(frac * n)
            data = self.chart.options["series"][0]["data"]
            for i in range(n):
                color = self.segment_colors[i] if i < lit else _LED_BAR_OFF_COLOR
                data[i]["itemStyle"]["color"] = color
            self.chart.update()
        elif self.display_type == "ring_gauge":
            # value ist hier eine 4er-Liste (reichweite_km_je_modus) statt
            # eines Skalars -- ein Wert je Ring/Fahrmodus.
            series = self.chart.options["series"]
            for i, v in enumerate(value[:len(series)]):
                series[i]["data"][0]["value"] = v
                self.legend_labels[i].set_text(f"{_RING_MODE_NAMES[i]}: {v} km")
            self.chart.update()
        elif self.display_type == "number":
            unit = self.cfg.get("unit", "")
            v = round(value, 1) if isinstance(value, float) else value
            self.value_label.set_text(f"{v} {unit}".strip())
        elif self.display_type == "badge":
            # Kategoriale Werte (aktuell nur "fahrmodus") ueber eine
            # Index->Name-Zuordnung aufloesen -- eigene Config-Angabe hat
            # Vorrang, sonst Fallback auf decode_bes3._MODI_DEFAULT (keine
            # Duplizierung der Namensliste).
            names = self.cfg.get("names")
            if names is None and self.signal == "fahrmodus":
                names = d._MODI_DEFAULT
            text = names.get(value, str(value)) if names else str(value)
            self.value_label.set_text(text)

    def _tick_clock_text(self):
        self.value_label.set_text(datetime.now().strftime("%H:%M:%S"))

    def _tick_clock_analog(self):
        now = datetime.now()
        hour_val = (now.hour % 12) + now.minute / 60
        minute_val = now.minute + now.second / 60
        second_val = now.second
        series = self.chart.options["series"]
        series[0]["data"][0]["value"] = hour_val
        series[1]["data"][0]["value"] = minute_val
        series[2]["data"][0]["value"] = second_val
        self.chart.update()


async def ble_task(tiles_by_signal, scan_timeout, reconnect_delay, state):
    """Laeuft dauerhaft im Hintergrund: liest Notifications, dekodiert sie
    und aktualisiert genau die Kacheln, fuer die ein Signal reinkommt.
    state["last_data_time"] wird bei JEDER Notification aktualisiert
    (unabhaengig davon, ob ein bekanntes Signal dekodiert wird) -- das
    "keine Daten"-Overlay in index() prueft diesen Zeitstempel."""
    async for _ts, data in c.stream_ble_notifications(scan_timeout, reconnect_delay):
        state["last_data_time"] = time.time()
        for name, value in d.decode_ble_frame(data).items():
            tile = tiles_by_signal.get(name)
            if tile is not None:
                tile.update(value)


# Plausible Testwerte fuers Anschauen des Layouts/der Farben ohne Bike in
# Reichweite (--demo). Signale ohne Eintrag hier fallen in demo_task() auf
# die Bereichsmitte (min/max) zurueck.
_DEMO_VALUES = {
    "geschwindigkeit_kmh": 18.5,
    "fahrer_leistung_w": 180,
    "motor_leistung_w": 320,
    "trittfrequenz_rpm": 85,
    "fet_temperatur_c": 62,
    "pcb_temperatur_c": 48,
    "ladezustand_prozent": 40,
    "hoehe_smartphone_m": 412,
    "fahrmodus": 3,  # eMTB in der Default-Namensliste
    "reichweite_km_je_modus": [120, 90, 65, 45],  # ECO/TOUR+/eMTB/TURBO
}


def demo_task(tiles_by_signal):
    """Setzt einmalig Testwerte auf alle konfigurierten Kacheln -- keine
    BLE-Verbindung noetig. Fuer Signale ohne Eintrag in _DEMO_VALUES wird bei
    gauge/vu_meter/led_bar die Bereichsmitte (min/max) verwendet."""
    for name, tile in tiles_by_signal.items():
        value = _DEMO_VALUES.get(name)
        if value is None:
            vmin, vmax = tile.cfg.get("min", 0), tile.cfg.get("max", 100)
            value = (vmin + vmax) / 2
        tile.update(value)


def build_app(config, scan_timeout, reconnect_delay, demo=False):
    tiles_by_signal = {}
    state = {"task": None, "last_data_time": time.time()}

    display_cfg = config.get("display", {})
    bg_color = display_cfg.get("background_color")
    tile_bg_color = display_cfg.get("tile_background_color")
    text_color = display_cfg.get("text_color")
    gauge_text_color = display_cfg.get("gauge_text_color")
    scale = display_cfg.get("scale", 1.0)

    @ui.page("/")
    def index():
        if bg_color:
            # ui.dark_mode() statt nur die Hintergrundfarbe zu setzen: passt
            # Quasars Text-/Komponentenfarben stimmig mit an. Ohne das
            # bliebe z. B. Text weiterhin dunkel und waere auf einem
            # dunklen Hintergrund unlesbar. Eine explizit HELLE
            # background_color waere damit allerdings unpassend --
            # gedacht fuer den ueblichen Fall "dunkles Cockpit".
            ui.dark_mode(True)
            ui.query("body").style(f"background-color: {bg_color}")
        if scale != 1.0:
            # Skalierung ueber die Root-Schriftgroesse statt eines
            # CSS-transform: scale(...) -- dadurch skalieren alle
            # rem-basierten Groessen (Tailwind-Klassen wie w-56/h-48,
            # Schriftgroessen, Abstaende) sauber mit, statt dass ein
            # transform nachtraeglich Layout/Overflow durcheinanderbringt.
            ui.query("html").style(f"font-size: {scale * 100}%")

        if not demo:
            # "Keine Daten"-Overlay: fest oben auf der Seite, standardmaessig
            # unsichtbar. Ein ui.timer prueft jede Sekunde, wie lange die
            # letzte Notification her ist (state["last_data_time"], von
            # ble_task() bei JEDER Notification aktualisiert) -- ab
            # _NO_DATA_TIMEOUT_S einblenden inkl. laufendem Sekunden-Counter,
            # verschwindet automatisch wieder sobald neue Daten reinkommen.
            # Im --demo-Modus gibt es keine echte BLE-Verbindung, also hier
            # komplett weglassen (sonst wuerde es nach 10s dauerhaft anzeigen).
            with ui.element("div").style(
                "position: fixed; top: 0; left: 0; right: 0; z-index: 9999; "
                "padding: 0.75rem; background-color: #b00020; color: white; "
                "text-align: center; font-weight: bold;"
            ) as overlay:
                overlay_label = ui.label("")
            overlay.set_visibility(False)

            def check_no_data():
                elapsed = time.time() - state["last_data_time"]
                if elapsed >= _NO_DATA_TIMEOUT_S:
                    overlay_label.set_text(f"⚠ Keine Daten seit {int(elapsed)} Sekunden")
                    overlay.set_visibility(True)
                else:
                    overlay.set_visibility(False)

            ui.timer(1.0, check_no_data)

        # CSS-Mehrspaltenlayout (column-width) statt flex-wrap: eine Reihe
        # aus flex-Karten laesst bei ungleich hohen Kacheln Luecken (die
        # naechste Kachel rutscht trotzdem stur daneben statt die freie
        # Hoehe unter einer kleinen Kachel zu nutzen). Mit column-width
        # fliesst jede Kachel von oben nach unten in die naechste freie
        # Spalte -- kurze und lange Kacheln packen sich so von selbst
        # duenner, echtes "Masonry"-Verhalten ganz ohne JS-Bibliothek.
        # column-width in rem (nicht px), damit --scale (s.o.) es mitskaliert.
        # text_color wird hier (nicht pro Element) gesetzt und vererbt sich
        # per CSS an alle Kind-Elemente ohne eigene Farbangabe -- betrifft
        # also z. B. die grossen Werte/Badges/Legenden, NICHT die bewusst
        # gedaempften "text-gray-500"-Beschriftungen (die haben ihre eigene
        # Tailwind-Farbklasse und werden dadurch nicht ueberschrieben).
        wrapper_style = "column-width: 16.25rem; column-gap: 1rem; padding: 1rem; width: 100%"
        if text_color:
            wrapper_style += f"; color: {text_color}"
        with ui.element("div").style(wrapper_style):
            for gauge_cfg in config["gauges"]:
                card_style = "break-inside: avoid; margin-bottom: 1rem; width: 100%"
                if tile_bg_color:
                    card_style += f"; background-color: {tile_bg_color}"
                # Kopie, damit das Original-Config-Dict nicht veraendert
                # wird, und setdefault statt Ueberschreiben, damit ein pro
                # Kachel explizit gesetztes gauge_text_color Vorrang behaelt.
                gauge_cfg = dict(gauge_cfg)
                gauge_cfg.setdefault("gauge_text_color", gauge_text_color)
                with ui.card().classes("items-center").style(card_style):
                    tile = Tile(gauge_cfg)
                if "signal" in gauge_cfg:
                    tiles_by_signal[gauge_cfg["signal"]] = tile
        if demo:
            demo_task(tiles_by_signal)

    if demo:
        print("Demo-Modus: zeige Testwerte statt einer echten BLE-Verbindung.")
        return

    async def start_ble_task():
        state["task"] = asyncio.create_task(
            ble_task(tiles_by_signal, scan_timeout, reconnect_delay, state))

    async def stop_ble_task():
        # Eigene Task explizit VOR dem generischen NiceGUI-/uvicorn-Shutdown
        # abbrechen und deren Abschluss abwarten -- sonst versucht der
        # Event-Loop beim Herunterfahren zusaetzlich, denselben
        # Async-Generator in stream_ble_notifications() zu schliessen,
        # waehrend die Cancellation noch laeuft ("aclose(): asynchronous
        # generator is already running").
        task = state["task"]
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    app.on_startup(start_ble_task)
    app.on_shutdown(stop_ble_task)


def main():
    parser = argparse.ArgumentParser(description="BLE-Live-Cockpit (Bosch Smart System)")
    parser.add_argument("--config", default=os.path.join(BASE, "config.yaml"),
                         help="Pfad zur Anzeige-Konfiguration (Standard: config.yaml neben diesem Skript)")
    parser.add_argument("--scan-timeout", type=float, default=10.0,
                         help="Sekunden, die pro Versuch nach dem Geraet gesucht wird (Standard: 10)")
    parser.add_argument("--reconnect-delay", type=float, default=3.0,
                         help="Wartezeit in Sekunden vor einem erneuten Verbindungsversuch (Standard: 3)")
    parser.add_argument("--port", type=int, default=8080, help="Port des lokalen Webservers (Standard: 8080)")
    parser.add_argument("--demo", action="store_true",
                         help="Zeigt Testwerte auf allen Kacheln an, ohne echte BLE-Verbindung "
                              "(zum Anschauen des Layouts/der Farben ohne Bike in Reichweite)")
    args = parser.parse_args()

    config = load_config(args.config)
    build_app(config, args.scan_timeout, args.reconnect_delay, demo=args.demo)
    ui.run(port=args.port, title="BES3 BLE Cockpit", reload=False, show=True)


if __name__ in {"__main__", "__mp_main__"}:
    main()
