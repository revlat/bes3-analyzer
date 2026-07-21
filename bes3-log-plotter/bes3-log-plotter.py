#!/usr/bin/env python3
"""Plottet alle in decode_bes3.py bekannten BES3-Signale einer Aufnahme als
Subplots in einem gemeinsamen Bild — Visualisierung eines Logs und Proof für
die bisher gefundenen Signale/Skalierungen. Zusätzlich ein Text-Panel mit den
im Log gefundenen ASCII-Strings (Bauteil-Typcodes, konfigurierte
Fahrmodus-Liste, Seriennummern/Parameter-Codes u. ä.).

Nutzung:
    python3 bes3-log-plotter.py <log_completely_full.csv> [-o ausgabe.png]

Nutzt den gemeinsamen Dekoder aus ../bes3-decoder/decode_bes3.py -- daher
nur innerhalb des geklonten Repos lauffaehig, nicht als isolierte Einzeldatei.
"""
import argparse
import os
import sys
import textwrap

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, "..", "bes3-decoder"))
import decode_bes3 as d

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

MAX_EXTRA_STRINGS = 25
TEXT_PANEL_WRAP_WIDTH = 170  # Zeichen -- passt bei fontsize=8, monospace, in die 20"-Figurbreite


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("csv_path", help="CAN-Log als CSV (Timestamp,ID,DLC,Data) — "
                         "idealerweise eine *_completely_full.csv des bes3-logger")
    parser.add_argument("-o", "--output", help="Pfad der Ausgabe-PNG "
                         "(Standard: <csv_path ohne .csv>_signals.png)")
    parser.add_argument("--dpi", type=int, default=130, help="Auflösung der PNG (Standard: 130)")
    return parser.parse_args()


def build_text_panel(csv_path, modes):
    """Baut den Textblock fuers Panel: Bauteil-Typcodes, die in DIESER
    Aufnahme konfigurierte Fahrmodus-Liste, und weitere wiederkehrende
    ASCII-Strings (Seriennummern, Parameter-Codes, Region/Speed-Limit, ...).
    Siehe decode_bes3.extract_ascii_strings fuer die Rauschfilterung.

    Bricht jede logische Zeile hart auf TEXT_PANEL_WRAP_WIDTH Zeichen um,
    damit die Anzahl Bildzeilen vorher exakt bekannt ist (fuer die
    Panel-Hoehe in main()) statt sie ueber matplotlibs eigenes Auto-Wrapping
    nur zu schaetzen."""
    strings = d.extract_ascii_strings(csv_path)

    raw_lines = []
    found_components = {c: l for c, l in d.KNOWN_COMPONENT_CODES.items() if c in strings}
    if found_components:
        raw_lines.append("Bauteil-Typcodes: " + "   ".join(f"{c} = {l}" for c, l in found_components.items()))

    raw_lines.append("Fahrmodus-Liste (aus dieser Aufnahme, am Bike konfigurierbar): "
                     + ", ".join(f"{i}={n}" for i, n in modes.items()))

    rest = {s: v for s, v in strings.items() if s not in d.KNOWN_COMPONENT_CODES}
    if rest:
        top = sorted(rest.items(), key=lambda kv: -kv[1]["count"])
        shown = ", ".join(f"{s} [{d.describe_can_ids(v['can_ids'])}] ({v['count']}×)"
                          for s, v in top[:MAX_EXTRA_STRINGS])
        suffix = f"  [+{len(top) - MAX_EXTRA_STRINGS} weitere]" if len(top) > MAX_EXTRA_STRINGS else ""
        raw_lines.append("Weitere wiederkehrende Text-Strings (Seriennummern/Parameter-Codes/...; "
                         "in eckigen Klammern die CAN-ID(s), an die der String gebunden ist): "
                         + shown + suffix)

    wrapped = []
    for line in raw_lines:
        wrapped.extend(textwrap.wrap(line, width=TEXT_PANEL_WRAP_WIDTH,
                                      subsequent_indent="    ") or [""])
    return wrapped


def main():
    args = parse_args()

    if not os.path.isfile(args.csv_path):
        sys.exit(f"Datei nicht gefunden: {args.csv_path}")

    out_path = args.output or f"{os.path.splitext(args.csv_path)[0]}_signals.png"

    res = d.decode_file(args.csv_path)
    modes = d.extract_mode_list(args.csv_path)

    names = list(d.SIGNALS.keys())
    n = len(names)
    cols = 4
    rows = (n + cols - 1) // cols

    text_lines = build_text_panel(args.csv_path, modes)

    signal_row_inch = 3.2
    line_height_inch = 0.16
    text_panel_inch = 0.3 + line_height_inch * len(text_lines)
    text_ratio = text_panel_inch / signal_row_inch  # gleiche Einheit wie die Signal-Reihen (ratio=1)

    fig = plt.figure(figsize=(20, signal_row_inch * rows + text_panel_inch + 0.7))
    gs = fig.add_gridspec(rows + 1, cols, height_ratios=[text_ratio] + [1] * rows)

    text_ax = fig.add_subplot(gs[0, :])
    text_ax.axis("off")
    text_ax.text(0.005, 0.98, "\n".join(text_lines), transform=text_ax.transAxes,
                 va="top", ha="left", fontsize=8, family="monospace")

    axes = [fig.add_subplot(gs[1 + i // cols, i % cols]) for i in range(rows * cols)]

    nonempty = [series[0][0] for series in res.values() if series]
    if not nonempty:
        sys.exit("Keine der bekannten Signale wurde in dieser Datei gefunden — "
                 "falsches Log-Format oder komplett unbekannter Frame-Aufbau?")
    t0 = min(nonempty)

    for ax, name in zip(axes, names):
        series = res[name]
        if not series:
            ax.set_title(f"{name}\n(keine Daten)", fontsize=9)
            ax.axis("off")
            continue
        ts = [(t - t0) / 60 for t, _ in series]  # Minuten seit Aufnahmebeginn
        vals = [v for _, v in series]
        if name == "fahrmodus":
            vals = [modes.get(v, v) for v in vals]
            # kategorial plotten: Mode-Namen als y-Achsen-Labels
            uniq = list(dict.fromkeys(modes.values()))
            y = [uniq.index(v) for v in vals]
            ax.step(ts, y, where="post", linewidth=1)
            ax.set_yticks(range(len(uniq)))
            ax.set_yticklabels(uniq, fontsize=7)
        else:
            ax.plot(ts, vals, linewidth=0.6, marker=".", markersize=1.5)
        ax.set_title(name, fontsize=9)
        ax.set_xlabel("min", fontsize=7)
        ax.tick_params(labelsize=7)
        ax.grid(alpha=0.3)

    # ungenutzte Subplots ausblenden
    for ax in axes[len(names):]:
        ax.axis("off")

    fig.suptitle(f"BES3 CAN-FD — alle bekannten Signale ({os.path.basename(args.csv_path)})",
                 fontsize=14)
    fig.tight_layout(rect=[0, 0, 1, 0.97])

    fig.savefig(out_path, dpi=args.dpi)
    print(f"gespeichert: {out_path}")


if __name__ == "__main__":
    main()
