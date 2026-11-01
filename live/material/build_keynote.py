#!/usr/bin/env python3
"""Build the synthetic Atlas Live keynote (M1c).

Generates, from the SCRIPT table below (single source of truth):
  - live/material/slides_en.html, slides_es.html (slides + speaker notes)
  - live/material/truth.json (claim-level ground truth, 41 claims x 2 langs)
  - data/keynote/keynote_en.mp4, keynote_es.mp4 (rendered videos, gitignored)
  - data/keynote/keynote_{en,es}.srt (subtitles from the script)
  - intermediates under data/keynote/work/ and data/keynote/charts/

Pipeline: SCRIPT -> matplotlib slide PNGs (+ 4 misleading charts) ->
piper TTS per slide -> ffmpeg segments -> concat -> mp4 + srt.

All local, $0. Never touches YouTube or any network at build time.

Usage: .venv/bin/python live/material/build_keynote.py
"""
import base64
import json
import os
import struct
import subprocess
import sys
import textwrap
import wave

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MATERIAL = os.path.join(ROOT, "live", "material")
OUTDIR = os.path.join(ROOT, "data", "keynote")
WORK = os.path.join(OUTDIR, "work")
CHARTS = os.path.join(OUTDIR, "charts")

PIPER_BIN = os.path.join(ROOT, ".venv", "bin", "piper")
VOICE = "/tmp/piper_voices_lj/en_US-ljspeech-medium.onnx"

WIDTH, HEIGHT, DPI = 1280, 720, 100
FPS = 30
PAD_AFTER_AUDIO = 0.6  # seconds of silence held on each slide

# ----------------------------------------------------------------------------
# SCRIPT: single source of truth. Each narration segment is either a planted
# claim (cid+T/F/U id, appears verbatim in truth.json) or filler (None).
# expected: "true" | "false" | "unverifiable"
# ----------------------------------------------------------------------------
SLIDES = [
    {
        "n": 1, "chart": None,
        "title": {"en": "The State of the World 2025", "es": "El estado del mundo 2025"},
        "bullets": {
            "en": ["World population: 8.14 billion (2024)",
                   "US GDP: 29.3 trillion dollars",
                   "Atlas Summit 2025"],
            "es": ["Población mundial: 8140 millones (2024)",
                   "PIB de EE. UU.: 29,3 billones de dólares",
                   "Cumbre Atlas 2025"],
        },
        "segs": [
            {"cid": "U1", "expected": "unverifiable",
             "ref": "n/a (opinion)",
             "en": "Welcome to the best summit on Earth.",
             "es": "Bienvenidos a la mejor cumbre del planeta."},
            {"cid": "U9", "expected": "unverifiable",
             "ref": "n/a (prediction about the talk itself)",
             "en": "This keynote will change how you see the world.",
             "es": "Esta charla cambiará su forma de ver el mundo."},
            {"cid": "T1", "expected": "true",
             "ref": "WB SP.POP.TOTL WLD 2024 = 8,140,897,523",
             "en": "We are 8.14 billion people sharing one planet, "
                   "according to the World Bank's 2024 population data.",
             "es": "Somos 8140 millones de personas en un solo planeta, "
                   "según los datos de población de 2024 del Banco Mundial."},
        ],
    },
    {
        "n": 2, "chart": None,
        "title": {"en": "A Crowded Planet", "es": "Un planeta lleno"},
        "bullets": {
            "en": ["China: 1.41 billion people",
                   "Brazil: 212 million people",
                   "Growth: millions of new people every year"],
            "es": ["China: 1410 millones de personas",
                   "Brasil: 212 millones de personas",
                   "Crecimiento: millones de personas nuevas cada año"],
        },
        "segs": [
            {"cid": "T2", "expected": "true",
             "ref": "WB SP.POP.TOTL CHN 2024 = 1,408,975,000",
             "en": "China is home to 1.41 billion people, "
                   "the World Bank reported for 2024.",
             "es": "China tiene 1410 millones de habitantes, "
                   "según el Banco Mundial en 2024."},
            {"cid": "T9", "expected": "true",
             "ref": "WB SP.POP.TOTL BRA 2024 = 211,998,573",
             "en": "Brazil has 212 million people, also in 2024.",
             "es": "Brasil tiene 212 millones de habitantes, también en 2024."},
            {"cid": "F13", "expected": "false",
             "ref": "WB BRA pop is a LEVEL (212M total); yearly change is "
                    "a rate (~2M/yr), not 212M/yr. Level-vs-rate confusion.",
             "en": "That means Brazil gains 212 million new people "
                   "every single year.",
             "es": "Eso significa que Brasil gana 212 millones de personas "
                   "nuevas cada año."},
            {"cid": "U3", "expected": "unverifiable",
             "ref": "n/a (vague, no measurable content)",
             "en": "Our planet has never been more connected.",
             "es": "Nuestro planeta nunca estuvo tan conectado."},
        ],
    },
    {
        "n": 3, "chart": None,
        "title": {"en": "The American Engine", "es": "El motor americano"},
        "bullets": {
            "en": ["US GDP 2024: 29.3 trillion dollars",
                   "US population: 340 million",
                   "20 trillion milestone: passed in 2012"],
            "es": ["PIB de EE. UU. 2024: 29,3 billones de dólares",
                   "Población de EE. UU.: 340 millones",
                   "Hito de 20 billones: superado en 2012"],
        },
        "segs": [
            {"cid": "T3", "expected": "true",
             "ref": "WB NY.GDP.MKTP.CD USA 2024 = 29,298,013,000,000",
             "en": "The United States produced 29.3 trillion dollars "
                   "of GDP in 2024.",
             "es": "Estados Unidos produjo 29,3 billones de dólares "
                   "de PIB en 2024."},
            {"cid": "T4", "expected": "true",
             "ref": "WB SP.POP.TOTL USA 2024 = 340,003,797",
             "en": "Its population reached 340 million people that same year.",
             "es": "Su población llegó a 340 millones de personas ese mismo año."},
            {"cid": "F1", "expected": "false",
             "ref": "WB USA GDP: 2017 = $19.61T, 2018 = $20.66T. "
                    "First passed $20T in 2018, not 2012 (swapped year).",
             "en": "America first passed 20 trillion dollars of GDP back in 2012, "
                   "years ahead of everyone else.",
             "es": "Estados Unidos superó por primera vez los 20 billones de dólares "
                   "de PIB en 2012, años antes que los demás."},
        ],
    },
    {
        "n": 4, "chart": None,
        "title": {"en": "Europe's Pillars", "es": "Los pilares de Europa"},
        "bullets": {
            "en": ["Germany GDP: 4.69 trillion dollars",
                   "France population: 68.6 million",
                   "France GDP: 4.69 trillion dollars"],
            "es": ["PIB de Alemania: 4,69 billones de dólares",
                   "Población de Francia: 68,6 millones",
                   "PIB de Francia: 4,69 billones de dólares"],
        },
        "segs": [
            {"cid": "T5", "expected": "true",
             "ref": "WB NY.GDP.MKTP.CD DEU 2024 = 4,685,592,577,805",
             "en": "Germany's GDP stood at 4.69 trillion dollars in 2024.",
             "es": "El PIB de Alemania fue de 4,69 billones de dólares en 2024."},
            {"cid": "T6", "expected": "true",
             "ref": "WB SP.POP.TOTL FRA 2024 = 68,551,653",
             "en": "France had 68.6 million inhabitants in 2024.",
             "es": "Francia tenía 68,6 millones de habitantes en 2024."},
            {"cid": "F2", "expected": "false",
             "ref": "WB NY.GDP.MKTP.CD FRA 2024 = $3.16T. $4.69T is "
                    "GERMANY's GDP (neighboring-entity swap).",
             "en": "And France's GDP was also 4.69 trillion dollars, "
                   "exactly matching its neighbor.",
             "es": "Y el PIB de Francia también fue de 4,69 billones de dólares, "
                   "igualando exactamente a su vecino."},
        ],
    },
    {
        "n": 5, "chart": None,
        "title": {"en": "Asia Rising", "es": "Asia en ascenso"},
        "bullets": {
            "en": ["Japan GDP: 4.19 trillion dollars",
                   "China population: 141 million",
                   "Japan GDP: 41.9 trillion dollars"],
            "es": ["PIB de Japón: 4,19 billones de dólares",
                   "Población de China: 141 millones",
                   "PIB de Japón: 41,9 billones de dólares"],
        },
        "segs": [
            {"cid": "U4", "expected": "unverifiable",
             "ref": "n/a (vague attribution, 'many experts')",
             "en": "Many experts agree this decade belongs to Asia.",
             "es": "Muchos expertos coinciden en que esta década pertenece a Asia."},
            {"cid": "T7", "expected": "true",
             "ref": "WB NY.GDP.MKTP.CD JPN 2024 = 4,190,008,188,359",
             "en": "Japan's GDP reached 4.19 trillion dollars in 2024.",
             "es": "El PIB de Japón alcanzó los 4,19 billones de dólares en 2024."},
            {"cid": "F3", "expected": "false",
             "ref": "WB CHN pop 2024 = 1,408,975,000 (1.41 BILLION). "
                    "141M is off by x10.",
             "en": "Meanwhile China's population has fallen to just 141 million people.",
             "es": "Mientras tanto, la población de China cayó a solo 141 millones "
                   "de personas."},
            {"cid": "F4", "expected": "false",
             "ref": "WB JPN GDP 2024 = $4.19T. 41.9T is off by x10 "
                    "(larger than the whole US economy).",
             "en": "And Japan's economy has exploded to 41.9 trillion dollars.",
             "es": "Y la economía de Japón se disparó hasta los 41,9 billones "
                   "de dólares."},
        ],
    },
    {
        "n": 6, "chart": "truncated",
        "title": {"en": "The Growth Illusion", "es": "La ilusión del crecimiento"},
        "bullets": {
            "en": ["US GDP 2023: 27.8 trillion",
                   "US GDP 2024: 29.3 trillion",
                   "Growth: the economy doubled"],
            "es": ["PIB de EE. UU. 2023: 27,8 billones",
                   "PIB de EE. UU. 2024: 29,3 billones",
                   "Crecimiento: la economía se duplicó"],
        },
        "segs": [
            {"cid": "T10", "expected": "true",
             "ref": "WB NY.GDP.MKTP.CD USA 2023 = 27,811,517,000,000",
             "en": "In 2023, American GDP was 27.8 trillion dollars.",
             "es": "En 2023, el PIB de Estados Unidos fue de 27,8 billones "
                   "de dólares."},
            {"cid": "F5", "expected": "false",
             "ref": "WB USA GDP 27.81T (2023) -> 29.30T (2024): +5.4%, "
                    "not x2. Chart uses a truncated Y axis (27-30).",
             "en": "One year later it had doubled, as this chart clearly shows.",
             "es": "Un año después se había duplicado, como muestra claramente "
                   "este gráfico."},
            {"cid": "U5", "expected": "unverifiable",
             "ref": "n/a (opinion)",
             "en": "This is the most important chart you will see today.",
             "es": "Este es el gráfico más importante que verán hoy."},
        ],
    },
    {
        "n": 7, "chart": "rate40",
        "title": {"en": "Percent Games", "es": "Juegos de porcentajes"},
        "bullets": {
            "en": ["8 billion milestone: November 2022",
                   "Population growth last year: 40%",
                   "8 billion milestone: 2020"],
            "es": ["Hito de 8000 millones: noviembre de 2022",
                   "Crecimiento demográfico del último año: 40%",
                   "Hito de 8000 millones: 2020"],
        },
        "segs": [
            {"cid": "T16", "expected": "true",
             "ref": "UN: world passed 8 billion on 15 Nov 2022.",
             "en": "Humanity passed 8 billion people in November 2022, "
                   "according to the United Nations.",
             "es": "La humanidad superó los 8000 millones de personas en noviembre "
                   "de 2022, según las Naciones Unidas."},
            {"cid": "F14", "expected": "false",
             "ref": "UN Day of 8 Billion was 15 Nov 2022, not 2020 "
                    "(swapped year; contradicts T16 in this same talk).",
             "en": "The world actually passed 8 billion people back in 2020.",
             "es": "En realidad, el mundo superó los 8000 millones en 2020."},
            {"cid": "F6", "expected": "false",
             "ref": "WB WLD pop 2023 = 8,062,923,417; 2024 = 8,140,897,523: "
                    "+0.97%, not 40%. The slide chart shows 0.97%, "
                    "contradicting the narration.",
             "en": "Last year alone, world population grew by nearly 40 percent.",
             "es": "Solo el año pasado, la población mundial creció casi "
                   "un 40 por ciento."},
            {"cid": "U6", "expected": "unverifiable",
             "ref": "n/a (future + vague)",
             "en": "Our children will thank us for these numbers.",
             "es": "Nuestros hijos nos agradecerán estas cifras."},
        ],
    },
    {
        "n": 8, "chart": None,
        "title": {"en": "Laws of Nature", "es": "Leyes de la naturaleza"},
        "bullets": {
            "en": ["Light speed: 299,792,458 m/s",
                   "Water boils: 100 C at sea level",
                   "Light speed: 29,979 km/s"],
            "es": ["Velocidad de la luz: 299792458 m/s",
                   "El agua hierve: 100 C al nivel del mar",
                   "Velocidad de la luz: 29979 km/s"],
        },
        "segs": [
            {"cid": "T11", "expected": "true",
             "ref": "SI defining constant (BIPM): c = 299,792,458 m/s, exact.",
             "en": "Light travels at exactly 299,792,458 meters per second.",
             "es": "La luz viaja a exactamente 299792458 metros por segundo."},
            {"cid": "T12", "expected": "true",
             "ref": "Standard physics: 100 C at 1 atm (sea level).",
             "en": "Water boils at 100 degrees Celsius at sea level.",
             "es": "El agua hierve a 100 grados Celsius al nivel del mar."},
            {"cid": "F7", "expected": "false",
             "ref": "c = 299,792 km/s. 29,979 km/s is off by x10.",
             "en": "Or in friendlier units, light moves at 29,979 kilometers "
                   "per second.",
             "es": "O en unidades más amables, la luz se mueve a 29979 kilómetros "
                   "por segundo."},
            {"cid": "F8", "expected": "false",
             "ref": "Water boils at 100 C at 1 atm. 1,000 C is off by x10.",
             "en": "And in a strong kettle, water can boil at 1,000 degrees.",
             "es": "Y en una tetera potente, el agua puede hervir a 1000 grados."},
        ],
    },
    {
        "n": 9, "chart": None,
        "title": {"en": "Mountains and Deserts", "es": "Montañas y desiertos"},
        "bullets": {
            "en": ["Everest: 8,849 meters",
                   "Sahara: 9.2 million square km",
                   "Sahara: 920,000 square km"],
            "es": ["Everest: 8849 metros",
                   "Sáhara: 9,2 millones de km cuadrados",
                   "Sáhara: 920000 km cuadrados"],
        },
        "segs": [
            {"cid": "T14", "expected": "true",
             "ref": "8,848.86 m (China-Nepal joint survey, 2020).",
             "en": "Mount Everest rises 8,849 meters above sea level.",
             "es": "El monte Everest se eleva 8849 metros sobre el nivel del mar."},
            {"cid": "T15", "expected": "true",
             "ref": "Largest hot desert, ~9.2M sq km (Britannica).",
             "en": "The Sahara, the largest hot desert, spans 9.2 million "
                   "square kilometers.",
             "es": "El Sáhara, el desierto cálido más grande, abarca 9,2 millones "
                   "de kilómetros cuadrados."},
            {"cid": "F9", "expected": "false",
             "ref": "Everest summit sits on the Nepal-CHINA border, "
                    "not entirely within Nepal (neighboring-entity error).",
             "en": "Everest, by the way, lies entirely within the borders of Nepal.",
             "es": "El Everest, por cierto, se encuentra totalmente dentro de "
                   "las fronteras de Nepal."},
            {"cid": "F10", "expected": "false",
             "ref": "~9.2M sq km. 920,000 is off by x10.",
             "en": "And the Sahara covers a modest 920,000 square kilometers.",
             "es": "Y el Sáhara cubre unos modestos 920000 kilómetros cuadrados."},
        ],
    },
    {
        "n": 10, "chart": "broken",
        "title": {"en": "The Tiger Emerges", "es": "El tigre despierta"},
        "bullets": {
            "en": ["India GDP 2024: 3.76 trillion",
                   "USA GDP 2024: 29.3 trillion",
                   "India now larger than the USA"],
            "es": ["PIB de India 2024: 3,76 billones",
                   "PIB de EE. UU. 2024: 29,3 billones",
                   "India ya es más grande que EE. UU."],
        },
        "segs": [
            {"cid": "T8", "expected": "true",
             "ref": "WB NY.GDP.MKTP.CD IND 2024 = 3,760,813,470,501",
             "en": "India's GDP reached 3.76 trillion dollars in 2024.",
             "es": "El PIB de India alcanzó los 3,76 billones de dólares en 2024."},
            {"cid": "F11", "expected": "false",
             "ref": "WB 2024: IND $3.76T vs USA $29.30T. India is ~8x SMALLER. "
                    "Chart uses a broken scale that flips the visual ranking.",
             "en": "As this chart proves, India's economy is now larger than America's.",
             "es": "Como demuestra este gráfico, la economía de India ya es más "
                   "grande que la de Estados Unidos."},
            {"cid": "U7", "expected": "unverifiable",
             "ref": "n/a (vague, unfalsifiable)",
             "en": "Nobody can deny that progress is everywhere.",
             "es": "Nadie puede negar que el progreso está en todas partes."},
        ],
    },
    {
        "n": 11, "chart": "cherry",
        "title": {"en": "The Long Decade", "es": "La década larga"},
        "bullets": {
            "en": ["Japan: growth every year this decade",
                   "Earth circumference: 40,075 km",
                   "Chart shows: three quarters of 2020"],
            "es": ["Japón: crecimiento todos los años de la década",
                   "Circunferencia de la Tierra: 40075 km",
                   "El gráfico muestra: tres trimestres de 2020"],
        },
        "segs": [
            {"cid": "T13", "expected": "true",
             "ref": "NASA: equatorial circumference ~40,075 km.",
             "en": "All of this happens on a planet 40,075 kilometers around "
                   "at the equator.",
             "es": "Todo esto ocurre en un planeta de 40075 kilómetros "
                   "de circunferencia en el ecuador."},
            {"cid": "F12", "expected": "false",
             "ref": "WB JPN GDP fell 2021 ($5.23T) -> 2024 ($4.19T). "
                    "Chart cherry-picks Q2-Q4 2020 and frames it as "
                    "'every year this decade'.",
             "en": "Look at Japan: its economy has grown every single year "
                   "this decade.",
             "es": "Miren Japón: su economía creció todos y cada uno de los años "
                   "de esta década."},
            {"cid": "U8", "expected": "unverifiable",
             "ref": "n/a (future prediction)",
             "en": "In ten years, all these figures will look tiny.",
             "es": "Dentro de diez años, todas estas cifras parecerán minúsculas."},
        ],
    },
    {
        "n": 12, "chart": None,
        "title": {"en": "Thank You", "es": "Gracias"},
        "bullets": {
            "en": ["Next summit: Sydney, Australia",
                   "2030: a changed world",
                   "The data speaks for itself"],
            "es": ["Próxima cumbre: Sídney, Australia",
                   "2030: un mundo cambiado",
                   "Los datos hablan por sí solos"],
        },
        "segs": [
            {"cid": "F15", "expected": "false",
             "ref": "The capital of Australia is Canberra, not Sydney "
                    "(neighboring-entity confusion).",
             "en": "Next year we meet in Sydney, the capital of Australia.",
             "es": "El año próximo nos vemos en Sídney, la capital de Australia."},
            {"cid": "U2", "expected": "unverifiable",
             "ref": "n/a (future prediction)",
             "en": "By 2030, this summit will have changed the world.",
             "es": "Para 2030, esta cumbre habrá cambiado el mundo."},
            {"cid": "U10", "expected": "unverifiable",
             "ref": "n/a (vague platitude)",
             "en": "The data speaks for itself. Thank you, and good night.",
             "es": "Los datos hablan por sí solos. Gracias y buenas noches."},
        ],
    },
]


def narration(slide, lang):
    return " ".join(s[lang] for s in slide["segs"])


# ----------------------------------------------------------------------------
# Charts: 4 deliberately misleading props (matplotlib).
# ----------------------------------------------------------------------------
def make_charts():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    os.makedirs(CHARTS, exist_ok=True)
    out = {}

    # 1) Truncated Y axis: 27.8 -> 29.3 (+5.4%) drawn to look ~3x.
    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    ax.bar(["2023", "2024"], [27.8, 29.3], color=["#94a3b8", "#f5b301"])
    ax.set_ylim(27.0, 30.0)
    ax.set_ylabel("Trillion dollars (axis starts at 27)")
    ax.set_title("US GDP doubled in one year (!)")
    for x, v in (("2023", 27.8), ("2024", 29.3)):
        ax.text(x, v + 0.03, f"{v}", ha="center", fontsize=11)
    p = os.path.join(CHARTS, "chart_truncated.png")
    fig.tight_layout()
    fig.savefig(p, dpi=100)
    plt.close(fig)
    out["truncated"] = p

    # 2) Honest 0.97% chart that CONTRADICTS the spoken "40%".
    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    ax.bar(["Last year"], [0.97], color="#38bdf8")
    ax.set_ylim(0, 5)
    ax.set_ylabel("Percent (%)")
    ax.set_title("World population growth, last year")
    ax.text("Last year", 1.1, "0.97%", ha="center", fontsize=12, fontweight="bold")
    p = os.path.join(CHARTS, "chart_rate40.png")
    fig.tight_layout()
    fig.savefig(p, dpi=100)
    plt.close(fig)
    out["rate40"] = p

    # 3) Broken scale: India (3.76) drawn TALLER than USA (29.3).
    # Textbook trick: each bar lives on its own panel/scale, so the small
    # value fills its panel while the big value is a sliver on its panel.
    fig, (ax_top, ax_bot) = plt.subplots(
        2, 1, figsize=(6.4, 4.2), sharex=True,
        gridspec_kw={"hspace": 0.08, "height_ratios": [1, 2]})
    ax_bot.bar([0], [3.76], color="#f5b301")          # India fills bottom
    ax_bot.set_xlim(-0.6, 1.6)
    ax_bot.set_xticks([0, 1])
    ax_bot.set_xticklabels(["India", "USA"])
    ax_top.bar([1], [1.3], bottom=[28.0], color="#94a3b8")  # USA sliver on top
    ax_top.set_xlim(-0.6, 1.6)
    ax_top.set_ylim(27.5, 30.0)
    ax_bot.set_ylim(0, 4.2)
    ax_top.spines["bottom"].set_visible(False)
    ax_bot.spines["top"].set_visible(False)
    ax_top.tick_params(bottom=False, labelbottom=False)
    d = 0.015
    kwargs = dict(transform=ax_top.transAxes, color="k", clip_on=False)
    ax_top.plot((-d, +d), (-d, +d), **kwargs)
    ax_top.plot((1 - d, 1 + d), (-d, +d), **kwargs)
    kwargs.update(transform=ax_bot.transAxes)
    ax_bot.plot((-d, +d), (1 - d, 1 + d), **kwargs)
    ax_bot.plot((1 - d, 1 + d), (1 - d, 1 + d), **kwargs)
    fig.suptitle("GDP 2024: India vs USA (trillion dollars)")
    ax_bot.set_ylabel("Trillion dollars (broken scale!)")
    p = os.path.join(CHARTS, "chart_broken.png")
    fig.tight_layout()
    fig.savefig(p, dpi=100)
    plt.close(fig)
    out["broken"] = p

    # 4) Temporal cherry-pick: three quarters of 2020 sold as "every year".
    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    qs = ["2020-Q2", "2020-Q3", "2020-Q4"]
    g = [-8.2, 5.4, 2.8]  # illustrative quarterly rebound, see README
    bars = ax.bar(qs, g, color=["#ef4444", "#22c55e", "#22c55e"])
    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_ylabel("Quarterly growth (%)")
    ax.set_title("Japan: growth every year this decade")
    for b, v in zip(bars, g):
        ax.text(b.get_x() + b.get_width() / 2, v + (0.2 if v > 0 else -0.5),
                f"{v}%", ha="center", fontsize=11)
    p = os.path.join(CHARTS, "chart_cherry.png")
    fig.tight_layout()
    fig.savefig(p, dpi=100)
    plt.close(fig)
    out["cherry"] = p

    return out


# ----------------------------------------------------------------------------
# Slide PNGs (matplotlib full-slide figures, 1280x720).
# ----------------------------------------------------------------------------
BG, FG, ACCENT, MUTED = "#101820", "#f1f5f9", "#f5b301", "#94a3b8"


def render_slide_png(slide, lang, chart_paths):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.image as mpimg

    n = slide["n"]
    fig = plt.figure(figsize=(WIDTH / DPI, HEIGHT / DPI), dpi=DPI)
    fig.patch.set_facecolor(BG)
    has_chart = bool(slide["chart"])

    # Title
    fig.text(0.06, 0.96, slide["title"][lang], color=ACCENT,
             fontsize=32, fontweight="bold", va="top", ha="left")
    fig.text(0.06, 0.845, "ATLAS SUMMIT 2025  \u2022  Synthetic keynote (M1c)",
             color=MUTED, fontsize=13, va="top", ha="left")

    # Bullets (left column, or full width when no chart)
    y = 0.71
    for b in slide["bullets"][lang]:
        wrapped = textwrap.wrap(b, width=34 if has_chart else 60)
        fig.text(0.06, y, "\u2022  " + wrapped[0], color=FG,
                 fontsize=21, va="top", ha="left")
        y -= 0.075
        for cont in wrapped[1:]:
            fig.text(0.085, y, cont, color=FG, fontsize=21, va="top", ha="left")
            y -= 0.07
        y -= 0.02

    # Footer
    fig.text(0.06, 0.045, f"Slide {n} / {len(SLIDES)}", color=MUTED, fontsize=13)
    fig.text(0.94, 0.045, "EN" if lang == "en" else "ES", color=MUTED,
             fontsize=13, ha="right")

    # Chart image on the right
    if has_chart:
        img = mpimg.imread(chart_paths[slide["chart"]])
        ax = fig.add_axes([0.55, 0.12, 0.40, 0.62])
        ax.imshow(img)
        ax.axis("off")
        for spine in ax.spines.values():
            spine.set_visible(False)

    p = os.path.join(WORK, f"slide_{n:02d}_{lang}.png")
    fig.savefig(p, dpi=DPI, facecolor=BG)
    plt.close(fig)
    return p


# ----------------------------------------------------------------------------
# TTS (piper CLI) + wav checks.
# ----------------------------------------------------------------------------
def tts(text, wav_path):
    cmd = [PIPER_BIN, "--model", VOICE, "--output_file", wav_path]
    r = subprocess.run(cmd, input=text.encode("utf-8"),
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if r.returncode != 0:
        raise RuntimeError(f"piper failed: {r.stderr.decode()[-500:]}")
    return wav_path


def wav_info(wav_path):
    with wave.open(wav_path, "rb") as w:
        frames = w.getnframes()
        rate = w.getframerate()
        raw = w.readframes(frames)
    dur = frames / rate if rate else 0.0
    # RMS energy check (16-bit mono expected from piper)
    n = min(len(raw) // 2, rate * 60)
    peak = 0
    esum = 0
    for i in range(n):
        v = struct.unpack("<h", raw[2 * i:2 * i + 2])[0]
        esum += v * v
        if abs(v) > peak:
            peak = abs(v)
    rms = (esum / max(n, 1)) ** 0.5
    return {"frames": frames, "rate": rate, "dur": dur,
            "rms": rms, "peak": peak,
            "bytes": os.path.getsize(wav_path)}


# ----------------------------------------------------------------------------
# ffmpeg assembly (imageio-ffmpeg binary).
# ----------------------------------------------------------------------------
def ffmpeg_bin():
    from imageio_ffmpeg import get_ffmpeg_exe
    return get_ffmpeg_exe()


def run(cmd):
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {' '.join(cmd)}\n"
                           f"{r.stderr.decode()[-800:]}")
    return r


def build_segment(ff, png, wav, dur, seg_path):
    cmd = [ff, "-y", "-v", "error",
           "-loop", "1", "-framerate", str(FPS), "-t", f"{dur:.3f}",
           "-i", png, "-i", wav,
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
           "-af", f"apad=whole_dur={dur:.3f}",
           "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
           "-t", f"{dur:.3f}", "-movflags", "+faststart", seg_path]
    run(cmd)


def concat_segments(ff, seg_paths, final_path):
    lst = os.path.join(WORK, "concat_%s.txt" % os.path.basename(final_path))
    with open(lst, "w") as f:
        for s in seg_paths:
            f.write(f"file '{s}'\n")
    run([ff, "-y", "-v", "error", "-f", "concat", "-safe", "0",
         "-i", lst, "-c", "copy", "-movflags", "+faststart", final_path])


def srt_time(sec):
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    ms = int(round((sec - int(sec)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(path, timings, texts):
    with open(path, "w") as f:
        for i, ((t0, t1), tx) in enumerate(zip(timings, texts), 1):
            f.write(f"{i}\n{srt_time(t0)} --> {srt_time(t1)}\n{tx}\n\n")


# ----------------------------------------------------------------------------
# HTML slides + truth.json.
# ----------------------------------------------------------------------------
HTML_CSS = """
body{background:#0b0f14;color:#f1f5f9;font-family:DejaVu Sans,Verdana,sans-serif;
margin:0;padding:24px}
h1{color:#f5b301}.sub{color:#94a3b8;margin-top:-12px}
.slide{background:#101820;border:1px solid #334155;border-radius:12px;
padding:24px 28px;margin:24px auto;max-width:960px}
.slide h2{color:#f5b301;margin:0 0 4px}.kicker{color:#94a3b8;font-size:13px}
.slide ul{font-size:19px;line-height:1.7}
.slide img{max-width:100%;border-radius:8px;border:1px solid #334155}
.notes{color:#cbd5e1;font-size:14px;background:#0b0f14;border-radius:8px;
padding:12px 14px;line-height:1.6}
.tag{font-size:12px;color:#94a3b8}
"""


def write_html(lang, chart_paths, timings):
    parts = ["<!DOCTYPE html><html lang='%s'><head><meta charset='utf-8'>"
             "<title>Atlas Summit 2025 keynote (%s)</title><style>%s</style>"
             "</head><body>" % (lang, lang.upper(), HTML_CSS)]
    title = ("The State of the World 2025" if lang == "en"
             else "El estado del mundo 2025")
    parts.append(f"<h1>{title}</h1><p class='sub'>Atlas Summit 2025 "
                 f"\u2022 synthetic keynote (M1c) \u2022 {lang.upper()} "
                 f"\u2022 charts embedded as base64, no external assets</p>")
    for slide, (t0, t1) in zip(SLIDES, timings):
        parts.append(f"<div class='slide'><div class='kicker'>Slide {slide['n']} "
                     f"/ {len(SLIDES)} \u2022 {t0:.1f}s\u2013{t1:.1f}s</div>"
                     f"<h2>{slide['title'][lang]}</h2><ul>")
        for b in slide["bullets"][lang]:
            parts.append(f"<li>{b}</li>")
        parts.append("</ul>")
        if slide["chart"]:
            with open(chart_paths[slide["chart"]], "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            parts.append(f"<img alt='chart {slide['chart']}' "
                         f"src='data:image/png;base64,{b64}'>")
        parts.append(f"<p class='tag'>Speaker notes (narration, verbatim):</p>"
                     f"<div class='notes'>{narration(slide, lang)}</div></div>")
    parts.append("</body></html>")
    p = os.path.join(MATERIAL, f"slides_{lang}.html")
    with open(p, "w") as f:
        f.write("\n".join(parts))
    return p


def write_truth(timings_by_lang):
    rows = []
    for lang in ("en", "es"):
        for slide, (t0, _t1) in zip(SLIDES, timings_by_lang[lang]):
            for s in slide["segs"]:
                rows.append({
                    "id": f"{s['cid']}-{lang}",
                    "claim": s["cid"],
                    "lang": lang,
                    "slide": slide["n"],
                    "text": s[lang],
                    "t_aprox": round(t0, 2),
                    "expected": s["expected"],
                    "ref": s["ref"],
                })
    ids = [r["id"] for r in rows]
    assert len(ids) == len(set(ids)), "duplicate truth.json ids!"
    p = os.path.join(MATERIAL, "truth.json")
    with open(p, "w") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    return p, rows


# ----------------------------------------------------------------------------
# Main.
# ----------------------------------------------------------------------------
def main():
    os.makedirs(WORK, exist_ok=True)
    os.makedirs(OUTDIR, exist_ok=True)
    assert os.path.exists(PIPER_BIN), f"missing piper: {PIPER_BIN}"
    assert os.path.exists(VOICE + ".json"), f"missing voice: {VOICE}"
    ff = ffmpeg_bin()

    print("== charts ==", flush=True)
    chart_paths = make_charts()
    for k, v in chart_paths.items():
        print(f"  {k}: {v} ({os.path.getsize(v)} bytes)")

    timings_by_lang = {}
    for lang in ("en", "es"):
        print(f"== slides+tts [{lang}] ==", flush=True)
        timings = []
        texts = []
        segs = []
        t = 0.0
        for slide in SLIDES:
            n = slide["n"]
            png = render_slide_png(slide, lang, chart_paths)
            wav = os.path.join(WORK, f"narr_{n:02d}_{lang}.wav")
            tts(narration(slide, lang), wav)
            info = wav_info(wav)
            assert info["frames"] > 0, f"empty wav: {wav}"
            assert info["rms"] > 50, f"silent wav: {wav} rms={info['rms']:.1f}"
            dur = info["dur"] + PAD_AFTER_AUDIO
            print(f"  slide {n:02d}: wav {info['bytes']} bytes, "
                  f"{info['dur']:.1f}s audio, rms={info['rms']:.0f} "
                  f"-> seg {dur:.1f}s")
            seg = os.path.join(WORK, f"seg_{n:02d}_{lang}.mp4")
            build_segment(ff, png, wav, dur, seg)
            timings.append((t, t + dur))
            t += dur
            texts.append(narration(slide, lang))
            segs.append(seg)
        timings_by_lang[lang] = timings
        final = os.path.join(OUTDIR, f"keynote_{lang}.mp4")
        concat_segments(ff, segs, final)
        write_srt(os.path.join(OUTDIR, f"keynote_{lang}.srt"), timings, texts)
        print(f"  VIDEO {final}: {t:.1f}s total ({t/60:.2f} min)")
        assert 150 <= t <= 300, f"duration {t:.1f}s outside 2.5-5 min!"

    print("== html + truth ==", flush=True)
    for lang in ("en", "es"):
        print(" ", write_html(lang, chart_paths, timings_by_lang[lang]))
    tp, rows = write_truth(timings_by_lang)
    from collections import Counter
    c = Counter((r["lang"], r["expected"]) for r in rows)
    print(" ", tp, f"({len(rows)} rows, ids unique)")
    for k in sorted(c):
        print(f"   {k}: {c[k]}")
    print("DONE")


if __name__ == "__main__":
    sys.exit(main())


