#!/usr/bin/env python3
"""Corpus sintético ES Atlas Live (6 piezas, rama main, checkout compartido).

Genera, desde las tablas PIECES de este archivo (única fuente de verdad):

  - live/material/truth_es/<pieza>.json (6 truth.json, ~40 claims c/u, ES)
  - data/synth/es/<pieza>.mp4 (1080p, 25 fps, 2.5-5 min) + .srt
  - data/synth/es/<pieza>_clean.wav + <pieza>_snr{20,10,5}db.wav
  - data/synth/es/snap/ (snapshots WB + SHA256SUMS, ya descargados)

Pipeline por pieza: guion -> slides matplotlib 1920x1080 (badge "Synthetic
demo" quemado + presentador abstracto dibujado por código) -> TTS piper
carlfm (dominio público) -> ffmpeg -> mp4 + srt + wavs con ruido.

Piezas: keynote_economia (K1), keynote_ciencia (K2), panel_datos (P1, dos
voces: carlfm + variante ffmpeg documentada), clase_poblacion (C1),
clase_economia (C2), earnings_luminara (E1, empresa FICTICIA,
expected=coherent-internal).

Todo local ($0) salvo la medición WER/recall (transcribe API, ~$0.01/pieza).

Uso (repo root): .venv/bin/python live/material/build_corpus_es.py [--pieces K1,K2,...] [--measure-only] [--check]

Env: PIPER_BIN (def .venv/bin/piper), VOICE_ES (def /tmp/piper_voices_es/...),
MODEL_API_KEY (solo para la medición).
"""
import argparse
import hashlib
import json
import os
import struct
import subprocess
import sys
import textwrap
import wave

MATERIAL = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(MATERIAL))
sys.path.insert(0, os.path.join(MATERIAL))  # measure_wer
sys.path.insert(0, ROOT)  # live.oido

from measure_wer import (  # noqa: E402
    asr_recall,
    extract_keywords,
    srt_plain_text,
    wer_details,
)

OUTDIR = os.path.join(ROOT, "data", "synth", "es")
SNAP = os.path.join(OUTDIR, "snap")
TRUTH_ES = os.path.join(MATERIAL, "truth_es")
WORK = os.path.join(OUTDIR, "work")
CHARTS = os.path.join(OUTDIR, "charts")

PIPER_BIN = os.environ.get("PIPER_BIN", os.path.join(ROOT, ".venv", "bin", "piper"))
VOICE_ES = os.environ.get(
    "VOICE_ES", "/tmp/piper_voices_es/es_ES-carlfm-x_low.onnx")

WIDTH, HEIGHT, DPI = 1920, 1080, 100
FPS = 25
PAD_AFTER_AUDIO = 0.6
USD_PER_HOUR = 0.18
SNR_LEVELS = (20, 10, 5)  # dB, ruido blanco generado por código (semilla fija)
NOISE_SEED = 7

BG, FG, ACCENT, MUTED = "#101820", "#f1f5f9", "#f5b301", "#94a3b8"

# ----------------------------------------------------------------------------
# Guiones: PIECES[pid] = {title, role, slides=[{n, chart, title, bullets,
# avatar, segs=[{cid, category, expected, ref, text[, sources, calculation,
# ground_truth, voice}]}]}. chart=None o clave de CHART_BUILDERS.
# voice: "A" (carlfm) o "B" (variante ffmpeg, solo panel).
# ----------------------------------------------------------------------------
WB_API = "https://api.worldbank.org/v2"


def wb_src(url_path):
    return [{"url": WB_API + url_path, "accessed": "2026-11-02",
             "note": "primario: API Banco Mundial, snapshot en data/synth/es/snap/"}]


IMF_WEO = "https://www.imf.org/en/publications/weo/issues/2026/10/13/world-economic-outlook-october-2026"
BLS_EMPSIT = "https://www.bls.gov/news.release/pdf/empsit.pdf"


def imf_src(note):
    return [{"url": IMF_WEO, "accessed": "2026-11-02", "note": note}]


def bls_src(note):
    return [{"url": BLS_EMPSIT, "accessed": "2026-11-02", "note": note}]


PIECES = {}

# ============ K1: keynote economía "El pulso de la economía mundial" ========
PIECES["K1"] = {
    "file": "keynote_economia",
    "title": "El pulso de la economía mundial",
    "kind": "keynote",
    "role": "PRESENTADORA",
    "slides": [
        {"n": 1, "chart": None, "title": "El pulso de la economía mundial",
         "bullets": ["EE. UU.: 29,3 billones de dólares", "China: 18,73 billones",
                     "Alemania: 4,69 billones"], "avatar": "presentadora",
         "segs": [
             {"cid": "K1F1", "category": "f", "expected": "true",
              "ref": "WB NY.GDP.MKTP.CD USA 2024 = 29,298,013,000,000",
              "sources": wb_src("/country/USA/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "29.298T; dice 29,3T, dif 0,00 % -> TRUE",
              "text": "Estados Unidos produjo 29,3 billones de dólares de PIB en 2024."},
             {"cid": "K1F2", "category": "f", "expected": "false",
              "ref": "WB CHN 2024 = 18,729,668,435,848 (18,73T, no 21,7T)",
              "sources": wb_src("/country/CHN/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "18,73T; dice 21,7T, dif 16 % -> FALSE",
              "text": "China alcanzó un PIB de 21,7 billones de dólares en 2024."},
             {"cid": "K1A1", "category": "a", "expected": "true",
              "ref": "WB NY.GDP.MKTP.CD DEU 2024 = 4,685,592,577,805",
              "sources": wb_src("/country/DEU/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "4,686T; dice 4,69T, dif 0,1 % -> TRUE",
              "text": "Alemania registró un PIB de 4,69 billones de dólares en 2024."},
             {"cid": "K1C1", "category": "c", "expected": "false",
              "ref": "WB 2024: ESP 1,73T, ITA 2,38T; ni 2,9T ni supera a Italia",
              "sources": wb_src("/country/ESP;ITA/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "1,73T < 2,38T; cifra y ranking inventados -> FALSE",
              "text": "España llegó a 2,9 billones de dólares en 2024, por encima de Italia."},
         ]},
        {"n": 2, "chart": None, "title": "Los gigantes asiáticos",
         "bullets": ["Japón: 4,19 billones", "India: 3,76 billones",
                     "Japón sigue por delante"], "avatar": "presentadora",
         "segs": [
             {"cid": "K1A2", "category": "a", "expected": "true",
              "ref": "WB NY.GDP.MKTP.CD JPN 2024 = 4,190,008,188,359",
              "sources": wb_src("/country/JPN/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "4,190T; dice 4,19T -> TRUE",
              "text": "Japón generó 4,19 billones de dólares de PIB en 2024."},
             {"cid": "K1A3", "category": "a", "expected": "false",
              "ref": "WB IND 2024 = 3,760,813,470,501 (3,76T, no 4,76T)",
              "sources": wb_src("/country/IND/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "3,76T; dice 4,76T, dif 27 % -> FALSE",
              "text": "La India produjo 4,76 billones de dólares en 2024."},
             {"cid": "K1F3", "category": "f", "expected": "false",
              "ref": "WB 2024: IND 3,76T < JPN 4,19T; no superó a Japón",
              "sources": wb_src("/country/IND;JPN/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "3,76 < 4,19; el sorpasso no ocurrió -> FALSE",
              "text": "La India superó a Japón en PIB en 2024."},
             {"cid": "K1C2", "category": "c", "expected": "false",
              "ref": "WB IND 2024 = 3,76T; 7,5T es el doble inventado",
              "sources": wb_src("/country/IND/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "3,76T; dice 7,5T vía duplicación inventada -> FALSE",
              "text": "El PIB de la India se duplicó hasta 7,5 billones en 2024."},
         ]},
        {"n": 3, "chart": None, "title": "Europa en cifras",
         "bullets": ["Francia: 3,16 billones", "Italia: 2,38 billones",
                     "Canadá: 2,27 billones"], "avatar": "presentadora",
         "segs": [
             {"cid": "K1A4", "category": "a", "expected": "true",
              "ref": "WB NY.GDP.MKTP.CD FRA 2024 = 3,160,442,622,465",
              "sources": wb_src("/country/FRA/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "3,160T; dice 3,16T -> TRUE",
              "text": "Francia produjo 3,16 billones de dólares en 2024."},
             {"cid": "K1A5", "category": "a", "expected": "false",
              "ref": "WB ITA 2024 = 2,383,435,562,458 (2,38T, no 2,93T)",
              "sources": wb_src("/country/ITA/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "2,38T; dice 2,93T, dif 23 % -> FALSE",
              "text": "Italia produjo 2,93 billones de dólares en 2024."},
             {"cid": "K1E1", "category": "e", "expected": "true",
              "ref": "WB NY.GDP.MKTP.CD CAN 2024 = 2,270,076,189,683",
              "sources": wb_src("/country/CAN/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "2,270T; dice 2,27T, escala correcta -> TRUE",
              "text": "Canadá produjo 2,27 billones de dólares en 2024."},
             {"cid": "K1E4", "category": "e", "expected": "false",
              "ref": "WB MEX 2024 = 1,830,489,311,089 (1,83 BILLONES, no millones)",
              "sources": wb_src("/country/MEX/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "1,83T; dice 1830M (x1000 menos) -> FALSE",
              "text": "El PIB de México fue de 1830 millones de dólares en 2024."},
         ]},
        {"n": 4, "chart": None, "title": "América Latina",
         "bullets": ["Brasil: 2,19 billones", "México: 1,83 billones",
                     "Chile por debajo de Colombia"], "avatar": "presentadora",
         "segs": [
             {"cid": "K1F4", "category": "f", "expected": "true",
              "ref": "WB NY.GDP.MKTP.CD BRA 2024 = 2,185,821,610,689",
              "sources": wb_src("/country/BRA/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "2,186T; dice 2,19T, dif 0,2 % -> TRUE",
              "text": "Brasil generó 2,19 billones de dólares en 2024."},
             {"cid": "K1A6", "category": "a", "expected": "true",
              "ref": "WB NY.GDP.MKTP.CD MEX 2024 = 1,830,489,311,089",
              "sources": wb_src("/country/MEX/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "1,830T; dice 1,83T -> TRUE",
              "text": "México alcanzó 1,83 billones de dólares en 2024."},
             {"cid": "K1C3", "category": "c", "expected": "false",
              "ref": "WB ARG 2024 = 638,365,455,340 (0,64T); 1,28T inventado",
              "sources": wb_src("/country/ARG/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "0,64T; dice 1,28T vía duplicación inventada -> FALSE",
              "text": "Argentina duplicó su PIB hasta 1,28 billones en 2024."},
             {"cid": "K1F5", "category": "f", "expected": "false",
              "ref": "WB 2024: CHL 329B < COL 420B; Chile no superó a Colombia",
              "sources": wb_src("/country/CHL;COL/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "329B < 420B; el sorpasso no ocurrió -> FALSE",
              "text": "Chile superó a Colombia en PIB en 2024."},
         ]},
        {"n": 5, "chart": "growth_bars", "title": "Crecimiento 2023-2024",
         "bullets": ["EE. UU.: +5,3 %", "España: +6,6 %", "India: +7,4 %",
                     "Japón cayó un 4,4 %"], "avatar": "presentadora",
         "segs": [
             {"cid": "K1D1", "category": "d", "expected": "true",
              "ref": "WB USA 27,811,517,000,000 (2023) -> 29,298,013,000,000 (2024)",
              "sources": wb_src("/country/USA/indicator/NY.GDP.MKTP.CD?format=json&date=2023:2024"),
              "ground_truth": "+5,345 %; dice 5,3 % (1 decimal) -> TRUE",
              "calculation": "(29298013-27811517)/27811517 = 0,05345 = 5,345 %",
              "text": "El PIB de Estados Unidos creció un 5,3 por ciento entre 2023 y 2024."},
             {"cid": "K1D2", "category": "d", "expected": "false",
              "ref": "WB IND 3,500,906,030,645 -> 3,760,813,470,501: +7,424 %, no 12,4 %",
              "sources": wb_src("/country/IND/indicator/NY.GDP.MKTP.CD?format=json&date=2023:2024"),
              "ground_truth": "+7,424 %; dice 12,4 % -> FALSE",
              "calculation": "(3760813-3500906)/3500906 = 0,07424 = 7,424 %",
              "text": "El PIB de la India creció un 12,4 por ciento entre 2023 y 2024."},
             {"cid": "K1D3", "category": "d", "expected": "true",
              "ref": "WB ESP 1,619,481,980,720 -> 1,725,671,652,742: +6,557 %",
              "sources": wb_src("/country/ESP/indicator/NY.GDP.MKTP.CD?format=json&date=2023:2024"),
              "ground_truth": "+6,557 %; dice 6,6 % (1 decimal) -> TRUE",
              "calculation": "(1725672-1619482)/1619482 = 0,06557 = 6,557 %",
              "text": "La economía española creció un 6,6 por ciento entre 2023 y 2024."},
             {"cid": "K1D4", "category": "d", "expected": "false",
              "ref": "WB JPN 4,384,854,269,962 -> 4,190,008,188,359: -4,444 %, cayó",
              "sources": wb_src("/country/JPN/indicator/NY.GDP.MKTP.CD?format=json&date=2023:2024"),
              "ground_truth": "-4,444 %; dice +3,1 % y creció -> FALSE",
              "calculation": "(4190008-4384854)/4384854 = -0,04444 = -4,444 %",
              "text": "El PIB de Japón creció un 3,1 por ciento entre 2023 y 2024."},
         ]},
        {"n": 6, "chart": "truncated_usa", "title": "La ilusión del crecimiento",
         "bullets": ["EE. UU. 2023: 27,8 billones", "EE. UU. 2024: 29,3 billones",
                     "El gráfico exagera el salto"], "avatar": "presentadora",
         "segs": [
             {"cid": "K1A7", "category": "a", "expected": "true",
              "ref": "WB NY.GDP.MKTP.CD USA 2023 = 27,811,517,000,000",
              "sources": wb_src("/country/USA/indicator/NY.GDP.MKTP.CD?format=json&date=2023:2024"),
              "ground_truth": "27,812T; dice 27,8T -> TRUE",
              "text": "En 2023 el PIB estadounidense fue de 27,8 billones de dólares."},
             {"cid": "K1C4", "category": "c", "expected": "false",
              "ref": "WB: 27,81T -> 29,30T (+5,4 %, no x2); eje truncado 27-30",
              "sources": wb_src("/country/USA/indicator/NY.GDP.MKTP.CD?format=json&date=2023:2024"),
              "ground_truth": "+5,4 %; dice duplicado con gráfico tramposo -> FALSE",
              "text": "En un año la economía de Estados Unidos se duplicó, como ven."},
             {"cid": "K1E2", "category": "e", "expected": "true",
              "ref": "WB NY.GDP.MKTP.CD KOR 2024 = 1,875,388,209,407",
              "sources": wb_src("/country/KOR/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "1,875T; dice 1,88T, escala correcta -> TRUE",
              "text": "Corea del Sur produjo 1,88 billones de dólares en 2024."},
             {"cid": "K1E5", "category": "e", "expected": "false",
              "ref": "WB DEU 2024 = 83,516,593 (83,5M); 8,35M es x10 menos",
              "sources": wb_src("/country/DEU/indicator/SP.POP.TOTL?format=json&date=2024"),
              "ground_truth": "83,5M; dice 8,35M (x10) -> FALSE",
              "text": "Alemania tenía 8,35 millones de habitantes en 2024."},
         ]},
        {"n": 7, "chart": "broken_ind_usa", "title": "Proporciones",
         "bullets": ["EE. UU.: 29,3 billones", "India: 3,76 billones",
                     "Ojo con la escala rota"], "avatar": "presentadora",
         "segs": [
             {"cid": "K1D5", "category": "d", "expected": "true",
              "ref": "WB 2024: USA 29,298,013,000,000 / IND 3,760,813,470,501",
              "sources": wb_src("/country/USA;IND/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "779,03 %; dice 779 % -> TRUE",
              "calculation": "29298013/3760813 = 7,7903 = 779,03 %",
              "text": "En 2024 el PIB de Estados Unidos fue el 779 por ciento del de la India."},
             {"cid": "K1D6", "category": "d", "expected": "false",
              "ref": "WB 2024: DEU 4,685,592,577,805 / GBR 3,695,539,513,534 = 126,79 %",
              "sources": wb_src("/country/DEU;GBR/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "126,79 %; dice 175 % -> FALSE",
              "calculation": "4685593/3695540 = 1,2679 = 126,79 %",
              "text": "El PIB alemán fue el 175 por ciento del británico en 2024."},
             {"cid": "K1C5", "category": "c", "expected": "false",
              "ref": "WB 2024: IND 3,76T frente a USA 29,30T (8x menor); escala rota",
              "sources": wb_src("/country/USA;IND/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "India 8x menor; dice mayor con gráfico tramposo -> FALSE",
              "text": "Como demuestra el gráfico, la India ya supera a Estados Unidos."},
             {"cid": "K1A8", "category": "a", "expected": "false",
              "ref": "WB IDN 2024 = 1,396,301,788,462 (1,40T, no 1,94T)",
              "sources": wb_src("/country/IDN/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "1,40T; dice 1,94T, dif 39 % -> FALSE",
              "text": "Indonesia generó 1,94 billones de dólares en 2024."},
         ]},
        {"n": 8, "chart": None, "title": "El mundo en 2026",
         "bullets": ["FMI: +3,1 % mundial en 2026", "EE. UU.: paro 4,2 % en sep-2026",
                     "Fuentes: FMI y BLS"], "avatar": "presentadora",
         "segs": [
             {"cid": "K1B1", "category": "b", "expected": "true",
              "ref": "IMF WEO oct-2026: crecimiento mundial 3,1 % en 2026",
              "sources": imf_src("primario: WEO oct-2026; ground truth de dev.json F2D-B1, verificado 2026-11-01"),
              "ground_truth": "3,1 % mundial 2026 -> TRUE",
              "text": "El FMI proyecta un crecimiento mundial del 3,1 por ciento en 2026."},
             {"cid": "K1B2", "category": "b", "expected": "false",
              "ref": "Mismo WEO: 3,1 % mundial; 2,1 % es solo avanzadas",
              "sources": imf_src("primario, mismo WEO; refuta (3,1 % mundial frente a 2,1 %)"),
              "ground_truth": "3,1 %; dice 2,1 % -> FALSE",
              "text": "El FMI proyecta un crecimiento mundial del 2,1 por ciento en 2026."},
             {"cid": "K1B3", "category": "b", "expected": "true",
              "ref": "BLS empsit sep-2026: paro 4,2 %",
              "sources": bls_src("primario: Employment Situation sep-2026; ground truth de dev.json F2D-B3"),
              "ground_truth": "4,2 % -> TRUE",
              "text": "El desempleo en Estados Unidos fue del 4,2 por ciento en sep-2026."},
             {"cid": "K1B4", "category": "b", "expected": "false",
              "ref": "BLS empsit sep-2026: 4,2 % (subió desde 4,1 %), no 3,2 %",
              "sources": bls_src("primario, mismo boletín; refuta (4,2 % frente a 3,2 %)"),
              "ground_truth": "4,2 %; dice 3,2 % a la baja -> FALSE",
              "text": "El desempleo estadounidense bajó al 3,2 por ciento en sep-2026."},
         ]},
        {"n": 9, "chart": None, "title": "Más señales de 2026",
         "bullets": ["7,1 millones de parados en EE. UU.", "Reino Unido: 69,3 millones",
                     "Canadá: dólares, no euros"], "avatar": "presentadora",
         "segs": [
             {"cid": "K1B5", "category": "b", "expected": "true",
              "ref": "BLS empsit sep-2026: 7,1 millones de desempleados",
              "sources": bls_src("primario, mismo boletín; ground truth de dev.json F2D-B5"),
              "ground_truth": "7,1M -> TRUE",
              "text": "En sep-2026 había 7,1 millones de desempleados en Estados Unidos."},
             {"cid": "K1B6", "category": "b", "expected": "false",
              "ref": "BLS empsit sep-2026: 7,1M, no 8,1M",
              "sources": bls_src("primario, mismo boletín; refuta (7,1M frente a 8,1M)"),
              "ground_truth": "7,1M; dice 8,1M -> FALSE",
              "text": "Los parados en Estados Unidos sumaban 8,1 millones en sep-2026."},
             {"cid": "K1E3", "category": "e", "expected": "true",
              "ref": "WB SP.POP.TOTL GBR 2024 = 69,281,000",
              "sources": wb_src("/country/GBR/indicator/SP.POP.TOTL?format=json&date=2024"),
              "ground_truth": "69,28M; dice 69,3M, escala correcta -> TRUE",
              "text": "El Reino Unido tenía 69,3 millones de habitantes en 2024."},
             {"cid": "K1E6", "category": "e", "expected": "false",
              "ref": "WB CAN 2024 = 2,270,076,189,683 USD; euros != dólares",
              "sources": wb_src("/country/CAN/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "2,27T USD; dice euros (unidad errónea) -> FALSE",
              "text": "El PIB de Canadá fue de 2,27 billones de euros en 2024."},
         ]},
        {"n": 10, "chart": None, "title": "Cierre",
         "bullets": ["España: 1,73 billones", "Media España-Italia: 2,05 billones",
                     "Gracias por asistir"], "avatar": "presentadora",
         "segs": [
             {"cid": "K1F6", "category": "f", "expected": "true",
              "ref": "WB NY.GDP.MKTP.CD ESP 2024 = 1,725,671,652,742",
              "sources": wb_src("/country/ESP/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "1,726T; dice 1,73T -> TRUE",
              "text": "España produjo 1,73 billones de dólares en 2024."},
             {"cid": "K1C6", "category": "c", "expected": "false",
              "ref": "WB NGA 2024 = 252,261,880,140 (252B y cayendo); 500B inventado",
              "sources": wb_src("/country/NGA/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "252B a la baja; dice 500B al alza -> FALSE",
              "text": "Nigeria duplicó su PIB hasta 500000 millones de dólares en 2024."},
             {"cid": "K1D7", "category": "d", "expected": "true",
              "ref": "WB 2024: (ESP 1,725,671,652,742 + ITA 2,383,435,562,458) / 2",
              "sources": wb_src("/country/ESP;ITA/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "2,0546T; dice 2,05T -> TRUE",
              "calculation": "(1725672+2383436)/2 = 2054554M = 2,0546T",
              "text": "El PIB medio de España e Italia en 2024 fue de 2,05 billones."},
             {"cid": "K1D8", "category": "d", "expected": "false",
              "ref": "WB 2024: (CAN 41,262,329 + ARG 45,696,159) / 2 = 43,48M, no 48,5M",
              "sources": wb_src("/country/CAN;ARG/indicator/SP.POP.TOTL?format=json&date=2024"),
              "ground_truth": "43,48M; dice 48,5M -> FALSE",
              "calculation": "(41262329+45696159)/2 = 43479244",
              "text": "La población media de Canadá y Argentina en 2024 fue de 48,5 millones."},
         ]},
    ],
}

# ============ K2: keynote ciencia/salud "Salud y ciencia en cifras" =========
PIECES["K2"] = {
    "file": "keynote_ciencia",
    "title": "Salud y ciencia en cifras",
    "kind": "keynote",
    "role": "PRESENTADORA",
    "slides": [
        {"n": 1, "chart": None, "title": "Vivir más años",
         "bullets": ["España: 83,9 años", "Japón: 84,0 años",
                     "EE. UU.: 78,9 años"], "avatar": "presentadora",
         "segs": [
             {"cid": "K2A1", "category": "a", "expected": "true",
              "ref": "WB SP.DYN.LE00.IN ESP 2024 = 83,888",
              "sources": wb_src("/country/ESP/indicator/SP.DYN.LE00.IN?format=json&date=2024"),
              "ground_truth": "83,888; dice 83,9 -> TRUE",
              "text": "La esperanza de vida en España fue de 83,9 años en 2024."},
             {"cid": "K2A2", "category": "a", "expected": "true",
              "ref": "WB SP.DYN.LE00.IN JPN 2024 = 84,036",
              "sources": wb_src("/country/JPN/indicator/SP.DYN.LE00.IN?format=json&date=2024"),
              "ground_truth": "84,036; dice 84,0 -> TRUE",
              "text": "En Japón la esperanza de vida llegó a 84,0 años en 2024."},
             {"cid": "K2A3", "category": "a", "expected": "false",
              "ref": "WB USA 2024 = 78,890 (no supera los 84)",
              "sources": wb_src("/country/USA/indicator/SP.DYN.LE00.IN?format=json&date=2024"),
              "ground_truth": "78,9; dice más de 84 -> FALSE",
              "text": "La esperanza de vida en Estados Unidos superó los 84 años en 2024."},
             {"cid": "K2F1", "category": "f", "expected": "true",
              "ref": "WB SP.POP.TOTL NGA 2024 = 232,679,478",
              "sources": wb_src("/country/NGA/indicator/SP.POP.TOTL?format=json&date=2024"),
              "ground_truth": "232,68M; dice 232,7M -> TRUE",
              "text": "Nigeria tenía 232,7 millones de habitantes en 2024."},
         ]},
        {"n": 2, "chart": None, "title": "La infancia en cifras",
         "bullets": ["Japón: 2,4 por 1000", "Nigeria: 115,6 por 1000",
                     "Mundo: 37,4 por 1000"], "avatar": "presentadora",
         "segs": [
             {"cid": "K2A4", "category": "a", "expected": "true",
              "ref": "WB SH.DYN.MORT JPN 2024 = 2,4",
              "sources": wb_src("/country/JPN/indicator/SH.DYN.MORT?format=json&date=2024"),
              "ground_truth": "2,4 por 1000 -> TRUE",
              "text": "En Japón morían 2,4 de cada 1000 niños antes de los 5 años."},
             {"cid": "K2A5", "category": "a", "expected": "true",
              "ref": "WB SH.DYN.MORT NGA 2024 = 115,6",
              "sources": wb_src("/country/NGA/indicator/SH.DYN.MORT?format=json&date=2024"),
              "ground_truth": "115,6 por 1000 -> TRUE",
              "text": "En Nigeria morían 115,6 de cada 1000 niños antes de los 5 años."},
             {"cid": "K2A6", "category": "a", "expected": "false",
              "ref": "WB WLD 2024 = 37,4 por 1000 (no 17,4)",
              "sources": wb_src("/country/WLD/indicator/SH.DYN.MORT?format=json&date=2024"),
              "ground_truth": "37,4; dice 17,4 -> FALSE",
              "text": "La mortalidad infantil mundial bajó a 17,4 por cada 1000 en 2024."},
             {"cid": "K2F2", "category": "f", "expected": "false",
              "ref": "WB JPN 2024 = 123,975,371 (124,0M, no 104M)",
              "sources": wb_src("/country/JPN/indicator/SP.POP.TOTL?format=json&date=2024"),
              "ground_truth": "124,0M; dice 104M, dif 16 % -> FALSE",
              "text": "Japón tenía 104 millones de habitantes en 2024."},
         ]},
        {"n": 3, "chart": None, "title": "Constantes de la naturaleza",
         "bullets": ["Luz: 299792458 m/s", "Sonido: 343 m/s",
                     "Agua: 100 grados"], "avatar": "presentadora",
         "segs": [
             {"cid": "K2A7", "category": "a", "expected": "true",
              "ref": "Constante definitoria SI (BIPM): c exacta",
              "sources": [],
              "ground_truth": "299792458 m/s exactos -> TRUE",
              "text": "La luz viaja a exactamente 299792458 metros por segundo."},
             {"cid": "K2A8", "category": "a", "expected": "true",
              "ref": "Física estándar: 331,3 + 0,606 x 20 = 343,4 m/s",
              "sources": [],
              "ground_truth": "343,4; dice 343, dif 0,1 % -> TRUE",
              "text": "El sonido viaja a 343 metros por segundo en aire seco a 20 grados."},
             {"cid": "K2E1", "category": "e", "expected": "true",
              "ref": "Física estándar: 100 C a 1 atm; escala correcta",
              "sources": [],
              "ground_truth": "100 C; escala correcta -> TRUE",
              "text": "El agua hierve a 100 grados Celsius al nivel del mar."},
             {"cid": "K2E4", "category": "e", "expected": "false",
              "ref": "Hierve a 100 C a 1 atm; 1000 es x10",
              "sources": [],
              "ground_truth": "100; dice 1000 (x10) -> FALSE",
              "text": "En una olla potente el agua puede hervir a 1000 grados."},
         ]},
        {"n": 4, "chart": None, "title": "El planeta en cifras",
         "bullets": ["Everest: 8849 metros", "Sáhara: 9,2 millones de km2",
                     "Ecuador: 40075 km", "Japón: 124,0 millones"], "avatar": "presentadora",
         "segs": [
             {"cid": "K2A9", "category": "a", "expected": "true",
              "ref": "8848,86 m (medición conjunta China-Nepal, 2020)",
              "sources": [],
              "ground_truth": "8848,86; dice 8849 -> TRUE",
              "text": "El Everest se eleva 8849 metros sobre el nivel del mar."},
             {"cid": "K2A10", "category": "a", "expected": "false",
              "ref": "~9,2M km2 (Britannica); 920000 es x10 menos",
              "sources": [],
              "ground_truth": "9,2M; dice 920000 -> FALSE",
              "text": "El Sáhara cubre unos modestos 920000 kilómetros cuadrados."},
             {"cid": "K2F3", "category": "f", "expected": "true",
              "ref": "WB SP.POP.TOTL JPN 2024 = 123,975,371",
              "sources": wb_src("/country/JPN/indicator/SP.POP.TOTL?format=json&date=2024"),
              "ground_truth": "123,98M; dice 124,0M -> TRUE",
              "text": "Japón tenía 124,0 millones de habitantes en 2024."},
             {"cid": "K2E2", "category": "e", "expected": "true",
              "ref": "NASA: circunferencia ecuatorial ~40075 km; escala correcta",
              "sources": [],
              "ground_truth": "40075 km; escala correcta -> TRUE",
              "text": "La Tierra mide 40075 kilómetros de circunferencia en el ecuador."},
         ]},
        {"n": 5, "chart": "life_bars", "title": "Comparar para entender",
         "bullets": ["Japón menos EE. UU.: 5,1 años", "Suiza por encima de España",
                     "España: 48,8 millones"], "avatar": "presentadora",
         "segs": [
             {"cid": "K2D1", "category": "d", "expected": "true",
              "ref": "WB 2024: JPN 84,036 - USA 78,890 = 5,146",
              "sources": wb_src("/country/JPN;USA/indicator/SP.DYN.LE00.IN?format=json&date=2024"),
              "ground_truth": "5,146; dice 5,1 -> TRUE",
              "calculation": "84,036 - 78,890 = 5,146",
              "text": "Japón superó a Estados Unidos en 5,1 años de vida en 2024."},
             {"cid": "K2D2", "category": "d", "expected": "false",
              "ref": "WB 2024: ESP 83,888 < CHE 84,407; no superó a Suiza",
              "sources": wb_src("/country/ESP;CHE/indicator/SP.DYN.LE00.IN?format=json&date=2024"),
              "ground_truth": "83,9 < 84,4; el sorpasso no ocurrió -> FALSE",
              "calculation": "83,888 frente a 84,407",
              "text": "La esperanza de vida en España superó a la de Suiza en 2024."},
             {"cid": "K2E5", "category": "e", "expected": "false",
              "ref": "WB ESP 2024 = 48,848,840 (48,8M); 4,9M es x10 menos",
              "sources": wb_src("/country/ESP/indicator/SP.POP.TOTL?format=json&date=2024"),
              "ground_truth": "48,8M; dice 4,9M (x10) -> FALSE",
              "text": "España tenía 4,9 millones de habitantes en 2024."},
             {"cid": "K2C1", "category": "c", "expected": "false",
              "ref": "WB NGA 2024 = 54,635 (no 70)",
              "sources": wb_src("/country/NGA/indicator/SP.DYN.LE00.IN?format=json&date=2024"),
              "ground_truth": "54,6; dice más de 70, inventado -> FALSE",
              "text": "La esperanza de vida en Nigeria superó los 70 años en 2024."},
         ]},
        {"n": 6, "chart": "truncated_life", "title": "El espejismo",
         "bullets": ["Nigeria 2022: 54,1 años", "Nigeria 2024: 54,6 años",
                     "El eje empieza en 54"], "avatar": "presentadora",
         "segs": [
             {"cid": "K2C2", "category": "c", "expected": "false",
              "ref": "WB NGA 54,079 -> 54,635 (+1 %, no x2); eje truncado",
              "sources": wb_src("/country/NGA/indicator/SP.DYN.LE00.IN?format=json&date=2022:2024"),
              "ground_truth": "+1 %; dice duplicado con eje truncado -> FALSE",
              "text": "Como muestra el gráfico, la vida media en Nigeria se duplicó."},
             {"cid": "K2F4", "category": "f", "expected": "true",
              "ref": "WB SP.DYN.LE00.IN CHE 2024 = 84,407",
              "sources": wb_src("/country/CHE/indicator/SP.DYN.LE00.IN?format=json&date=2024"),
              "ground_truth": "84,407; dice 84,4 -> TRUE",
              "text": "La esperanza de vida en Suiza fue de 84,4 años en 2024."},
             {"cid": "K2F5", "category": "f", "expected": "false",
              "ref": "WB 2024: CHN 78,017 < USA 78,890; no superó a EE. UU.",
              "sources": wb_src("/country/CHN;USA/indicator/SP.DYN.LE00.IN?format=json&date=2024"),
              "ground_truth": "78,0 < 78,9; el sorpasso no ocurrió -> FALSE",
              "text": "China superó a Estados Unidos en esperanza de vida en 2024."},
             {"cid": "K2E3", "category": "e", "expected": "true",
              "ref": "WB SP.POP.TOTL KOR 2024 = 51,751,065",
              "sources": wb_src("/country/KOR/indicator/SP.POP.TOTL?format=json&date=2024"),
              "ground_truth": "51,75M; dice 51,8M, escala correcta -> TRUE",
              "text": "Corea del Sur tenía 51,8 millones de habitantes en 2024."},
         ]},
        {"n": 7, "chart": "mort_contrast", "title": "Mortalidad comparada",
         "bullets": ["Nigeria frente a Japón: x48", "Mundo: 37,4 por 1000",
                     "El gráfico dice 37,4"], "avatar": "presentadora",
         "segs": [
             {"cid": "K2D3", "category": "d", "expected": "true",
              "ref": "WB 2024: NGA 115,6 / JPN 2,4 = 48,17",
              "sources": wb_src("/country/NGA;JPN/indicator/SH.DYN.MORT?format=json&date=2024"),
              "ground_truth": "48,17x; dice 48 veces -> TRUE",
              "calculation": "115,6 / 2,4 = 48,17",
              "text": "La mortalidad infantil de Nigeria fue 48 veces la de Japón."},
             {"cid": "K2D4", "category": "d", "expected": "false",
              "ref": "WB 2024: WLD 37,4 frente a NGA 115,6 (3x menor, no doble)",
              "sources": wb_src("/country/WLD;NGA/indicator/SH.DYN.MORT?format=json&date=2024"),
              "ground_truth": "37,4 < 115,6; no es el doble -> FALSE",
              "calculation": "37,4 frente a 115,6",
              "text": "La mortalidad infantil mundial duplicó a la de Nigeria en 2024."},
             {"cid": "K2C3", "category": "c", "expected": "false",
              "ref": "WB WLD 2024 = 37,4 (el slide muestra 37,4); 3,7 es x10 menos",
              "sources": wb_src("/country/WLD/indicator/SH.DYN.MORT?format=json&date=2024"),
              "ground_truth": "37,4; dice 3,7 contra su propio slide -> FALSE",
              "text": "La mortalidad infantil mundial cayó a 3,7 por cada 1000 en 2024."},
             {"cid": "K2E6", "category": "e", "expected": "false",
              "ref": "WB JPN 2024 = 4,190,008,188,359 USD; euros != dólares",
              "sources": wb_src("/country/JPN/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "4,19T USD; dice euros (unidad errónea) -> FALSE",
              "text": "El PIB de Japón fue de 4,19 billones de euros en 2024."},
         ]},
        {"n": 8, "chart": None, "title": "Señales de 2026",
         "bullets": ["FMI avanzadas: +2,1 %", "EE. UU.: 340 millones",
                     "Canadá: 41,3 millones"], "avatar": "presentadora",
         "segs": [
             {"cid": "K2B1", "category": "b", "expected": "true",
              "ref": "IMF WEO oct-2026: avanzadas 2,1 % en 2026",
              "sources": imf_src("primario: WEO oct-2026; cifra de avanzadas citada en dev.json F2D-B2"),
              "ground_truth": "2,1 % avanzadas 2026 -> TRUE",
              "text": "El FMI prevé que las avanzadas crezcan un 2,1 por ciento en 2026."},
             {"cid": "K2B2", "category": "b", "expected": "false",
              "ref": "Mismo WEO: 3,4 % es 2027, no 2026 (año cambiado)",
              "sources": imf_src("primario, mismo WEO; refuta (3,4 % es de 2027)"),
              "ground_truth": "3,1 % en 2026; dice 3,4 % -> FALSE",
              "text": "El FMI prevé un crecimiento mundial del 3,4 por ciento en 2026."},
             {"cid": "K2F6", "category": "f", "expected": "true",
              "ref": "WB SP.POP.TOTL USA 2024 = 340,003,797",
              "sources": wb_src("/country/USA/indicator/SP.POP.TOTL?format=json&date=2024"),
              "ground_truth": "340,00M; dice 340M -> TRUE",
              "text": "Estados Unidos tenía 340 millones de habitantes en 2024."},
             {"cid": "K2F7", "category": "f", "expected": "false",
              "ref": "WB CAN 2024 = 41,262,329 (no 50M)",
              "sources": wb_src("/country/CAN/indicator/SP.POP.TOTL?format=json&date=2024"),
              "ground_truth": "41,3M; dice más de 50M -> FALSE",
              "text": "Canadá superó los 50 millones de habitantes en 2024."},
         ]},
        {"n": 9, "chart": None, "title": "Más 2026",
         "bullets": ["FMI 2027: +3,4 %", "Everest: frontera China-Nepal",
                     "China: 1409 millones"], "avatar": "presentadora",
         "segs": [
             {"cid": "K2B3", "category": "b", "expected": "true",
              "ref": "IMF WEO oct-2026: crecimiento mundial 3,4 % en 2027",
              "sources": imf_src("primario: WEO oct-2026; cifra 2027 citada en dev.json F2D-B1"),
              "ground_truth": "3,4 % mundial 2027 -> TRUE",
              "text": "El FMI proyecta un crecimiento mundial del 3,4 por ciento en 2027."},
             {"cid": "K2B4", "category": "b", "expected": "false",
              "ref": "BLS empsit sep-2026: 4,2 % (no 3,2 %)",
              "sources": bls_src("primario, mismo boletín; refuta (4,2 % frente a 3,2 %)"),
              "ground_truth": "4,2 %; dice 3,2 % -> FALSE",
              "text": "El paro en Estados Unidos cayó al 3,2 por ciento en sep-2026."},
             {"cid": "K2C4", "category": "c", "expected": "false",
              "ref": "La cima está en la frontera Nepal-CHINA, no solo en Nepal",
              "sources": [],
              "ground_truth": "Frontera compartida; dice solo Nepal -> FALSE",
              "text": "El Everest está íntegramente dentro de las fronteras de Nepal."},
             {"cid": "K2C5", "category": "c", "expected": "false",
              "ref": "WB CHN 2024 = 1,408,975,000 (1409M); 141M es x10 menos",
              "sources": wb_src("/country/CHN/indicator/SP.POP.TOTL?format=json&date=2024"),
              "ground_truth": "1409M; dice 141M, inventado -> FALSE",
              "text": "La población de China cayó a solo 141 millones de personas."},
         ]},
        {"n": 10, "chart": None, "title": "Cierre",
         "bullets": ["Media Alemania-Francia: 76,0 millones",
                     "México: 130,9 millones", "Mundo: 8141 millones"],
         "avatar": "presentadora",
         "segs": [
             {"cid": "K2D5", "category": "d", "expected": "true",
              "ref": "WB 2024: (DEU 83,516,593 + FRA 68,551,653) / 2 = 76,03M",
              "sources": wb_src("/country/DEU;FRA/indicator/SP.POP.TOTL?format=json&date=2024"),
              "ground_truth": "76,03M; dice 76,0M -> TRUE",
              "calculation": "(83516593+68551653)/2 = 76034123",
              "text": "La media de Alemania y Francia en 2024 fue de 76,0 millones."},
             {"cid": "K2D6", "category": "d", "expected": "false",
              "ref": "WB 2024: (KOR 1,875,388,209,407 + CAN 2,270,076,189,683) / 2 = 2,07T",
              "sources": wb_src("/country/KOR;CAN/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "2,07T; dice 2,57T -> FALSE",
              "calculation": "(1875388+2270076)/2 = 2072732M = 2,0727T",
              "text": "El PIB medio de Corea del Sur y Canadá fue de 2,57 billones."},
             {"cid": "K2F8", "category": "f", "expected": "true",
              "ref": "WB SP.POP.TOTL MEX 2024 = 130,861,007",
              "sources": wb_src("/country/MEX/indicator/SP.POP.TOTL?format=json&date=2024"),
              "ground_truth": "130,86M; dice 130,9M -> TRUE",
              "text": "México tenía 130,9 millones de habitantes en 2024."},
             {"cid": "K2C6", "category": "c", "expected": "false",
              "ref": "WB WLD 2024 = 8,140,897,523 (8141M, no 9000M)",
              "sources": wb_src("/country/WLD/indicator/SP.POP.TOTL?format=json&date=2024"),
              "ground_truth": "8141M; dice 9000M, inventado -> FALSE",
              "text": "La población mundial superó los 9000 millones en 2024."},
         ]},
    ],
}

# ============ P1: panel "Mesa redonda: datos que importan" (2 voces) ======
# voice A = carlfm (MODERADORA), voice B = variante ffmpeg (INVITADO).
PIECES["P1"] = {
    "file": "panel_datos",
    "title": "Mesa redonda: datos que importan",
    "kind": "panel",
    "role": "MODERADORA / INVITADO",
    "slides": [
        {"n": 1, "chart": None, "title": "Bienvenida a la mesa",
         "bullets": ["EE. UU.: 29,3 billones", "China: 1409 millones",
                     "India: 1451 millones"], "avatar": "panel",
         "segs": [
             {"cid": "P1F1", "category": "f", "expected": "true", "voice": "A",
              "ref": "WB NY.GDP.MKTP.CD USA 2024 = 29,298,013,000,000",
              "sources": wb_src("/country/USA/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "29,298T; dice 29,3T -> TRUE",
              "text": "Moderadora: Estados Unidos produjo 29,3 billones de dólares en 2024."},
             {"cid": "P1F2", "category": "f", "expected": "true", "voice": "B",
              "ref": "WB SP.POP.TOTL CHN 2024 = 1,408,975,000",
              "sources": wb_src("/country/CHN/indicator/SP.POP.TOTL?format=json&date=2024"),
              "ground_truth": "1408,98M; dice 1409M -> TRUE",
              "text": "Invitado: y China tiene 1409 millones de habitantes."},
             {"cid": "P1A1", "category": "a", "expected": "true", "voice": "A",
              "ref": "WB SP.POP.TOTL IND 2024 = 1,450,935,791",
              "sources": wb_src("/country/IND/indicator/SP.POP.TOTL?format=json&date=2024"),
              "ground_truth": "1450,94M; dice 1451M -> TRUE",
              "text": "Moderadora: La India tenía 1451 millones de habitantes en 2024."},
             {"cid": "P1C1", "category": "c", "expected": "false", "voice": "B",
              "ref": "WB 2024: ESP 1,73T < FRA 3,16T; 3,3T inventado",
              "sources": wb_src("/country/ESP;FRA/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "1,73T < 3,16T; cifra y sorpasso inventados -> FALSE",
              "text": "Invitado: España superó a Francia con 3,3 billones en 2024."},
         ]},
        {"n": 2, "chart": None, "title": "Asia a debate",
         "bullets": ["Japón: 4,19 billones", "China: +2,5 %",
                     "Indonesia: 283,5 millones"], "avatar": "panel",
         "segs": [
             {"cid": "P1A2", "category": "a", "expected": "true", "voice": "A",
              "ref": "WB NY.GDP.MKTP.CD JPN 2024 = 4,190,008,188,359",
              "sources": wb_src("/country/JPN/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "4,190T; dice 4,19T -> TRUE",
              "text": "Moderadora: Japón generó 4,19 billones de dólares en 2024."},
             {"cid": "P1F3", "category": "f", "expected": "false", "voice": "B",
              "ref": "WB 2024: IDN 1,40T < BRA 2,19T; no superó a Brasil",
              "sources": wb_src("/country/IDN;BRA/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "1,40 < 2,19; el sorpasso no ocurrió -> FALSE",
              "text": "Invitado: Indonesia superó a Brasil en PIB en 2024."},
             {"cid": "P1D1", "category": "d", "expected": "true", "voice": "A",
              "ref": "WB CHN 18,270,356,654,533 -> 18,729,668,435,848: +2,514 %",
              "sources": wb_src("/country/CHN/indicator/NY.GDP.MKTP.CD?format=json&date=2023:2024"),
              "ground_truth": "+2,514 %; dice 2,5 % -> TRUE",
              "calculation": "(18729668-18270357)/18270357 = 0,02514 = 2,514 %",
              "text": "Moderadora: El PIB chino creció un 2,5 por ciento entre 2023 y 2024."},
             {"cid": "P1C2", "category": "c", "expected": "false", "voice": "B",
              "ref": "WB IDN 2024 = 283,487,931 (283,5M, no 350M)",
              "sources": wb_src("/country/IDN/indicator/SP.POP.TOTL?format=json&date=2024"),
              "ground_truth": "283,5M; dice 350M, inventado -> FALSE",
              "text": "Invitado: Indonesia superó los 350 millones de habitantes en 2024."},
         ]},
        {"n": 3, "chart": None, "title": "Europa a debate",
         "bullets": ["Francia: 68,6 millones", "Italia: 2,38 billones",
                     "Alemania: 4,69 billones"], "avatar": "panel",
         "segs": [
             {"cid": "P1A3", "category": "a", "expected": "true", "voice": "A",
              "ref": "WB SP.POP.TOTL FRA 2024 = 68,551,653",
              "sources": wb_src("/country/FRA/indicator/SP.POP.TOTL?format=json&date=2024"),
              "ground_truth": "68,55M; dice 68,6M -> TRUE",
              "text": "Moderadora: Francia tenía 68,6 millones de habitantes en 2024."},
             {"cid": "P1A4", "category": "a", "expected": "false", "voice": "B",
              "ref": "WB DEU 2024 = 4,685,592,577,805 (4,69T, no 5,69T)",
              "sources": wb_src("/country/DEU/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "4,69T; dice 5,69T, dif 21 % -> FALSE",
              "text": "Invitado: Alemania produjo 5,69 billones de dólares en 2024."},
             {"cid": "P1E1", "category": "e", "expected": "true", "voice": "A",
              "ref": "WB NY.GDP.MKTP.CD ITA 2024 = 2,383,435,562,458",
              "sources": wb_src("/country/ITA/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "2,383T; dice 2,38T, escala correcta -> TRUE",
              "text": "Moderadora: Italia produjo 2,38 billones de dólares en 2024."},
             {"cid": "P1E4", "category": "e", "expected": "false", "voice": "B",
              "ref": "WB ITA 2024 = 2,38 BILLONES; 2380M es x1000 menos",
              "sources": wb_src("/country/ITA/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "2,38T; dice 2380M (x1000) -> FALSE",
              "text": "Invitado: El PIB italiano fue de 2380 millones de dólares en 2024."},
         ]},
        {"n": 4, "chart": None, "title": "América a debate",
         "bullets": ["Brasil: 212 millones", "México: 1,83 billones",
                     "Colombia por debajo de México"], "avatar": "panel",
         "segs": [
             {"cid": "P1F4", "category": "f", "expected": "true", "voice": "A",
              "ref": "WB SP.POP.TOTL BRA 2024 = 211,998,573",
              "sources": wb_src("/country/BRA/indicator/SP.POP.TOTL?format=json&date=2024"),
              "ground_truth": "212,00M; dice 212M -> TRUE",
              "text": "Moderadora: Brasil tenía 212 millones de habitantes en 2024."},
             {"cid": "P1A5", "category": "a", "expected": "true", "voice": "B",
              "ref": "WB NY.GDP.MKTP.CD MEX 2024 = 1,830,489,311,089",
              "sources": wb_src("/country/MEX/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "1,830T; dice 1,83T -> TRUE",
              "text": "Invitado: México generó 1,83 billones de dólares en 2024."},
             {"cid": "P1C3", "category": "c", "expected": "false", "voice": "A",
              "ref": "WB MEX 2024 = 1,83T; 3,66T es el doble inventado",
              "sources": wb_src("/country/MEX/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "1,83T; dice 3,66T, inventado -> FALSE",
              "text": "Moderadora: El PIB de México se duplicó hasta 3,66 billones."},
             {"cid": "P1F5", "category": "f", "expected": "false", "voice": "B",
              "ref": "WB 2024: COL 420B < MEX 1830B; no superó a México",
              "sources": wb_src("/country/COL;MEX/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "420B < 1830B; el sorpasso no ocurrió -> FALSE",
              "text": "Invitado: Colombia superó a México en PIB en 2024."},
         ]},
        {"n": 5, "chart": "growth_bars", "title": "Cuentas claras",
         "bullets": ["México: +2,0 %", "Alemania: +2,7 %",
                     "Canadá: 41,3 millones"], "avatar": "panel",
         "segs": [
             {"cid": "P1D2", "category": "d", "expected": "false", "voice": "A",
              "ref": "WB DEU 4,562,207,532,490 -> 4,685,592,577,805: +2,705 %, no 12,7 %",
              "sources": wb_src("/country/DEU/indicator/NY.GDP.MKTP.CD?format=json&date=2023:2024"),
              "ground_truth": "+2,705 %; dice 12,7 % -> FALSE",
              "calculation": "(4685593-4562208)/4562208 = 0,02705 = 2,705 %",
              "text": "Moderadora: El PIB alemán creció un 12,7 por ciento entre 2023 y 2024."},
             {"cid": "P1D3", "category": "d", "expected": "true", "voice": "B",
              "ref": "WB MEX 1,794,410,347,718 -> 1,830,489,311,089: +2,011 %",
              "sources": wb_src("/country/MEX/indicator/NY.GDP.MKTP.CD?format=json&date=2023:2024"),
              "ground_truth": "+2,011 %; dice 2,0 % -> TRUE",
              "calculation": "(1830489-1794410)/1794410 = 0,02011 = 2,011 %",
              "text": "Invitado: El PIB mexicano creció un 2,0 por ciento entre 2023 y 2024."},
             {"cid": "P1A6", "category": "a", "expected": "false", "voice": "A",
              "ref": "WB CAN 2024 = 2,270,076,189,683 (2,27T, no 3,27T)",
              "sources": wb_src("/country/CAN/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "2,27T; dice 3,27T, dif 44 % -> FALSE",
              "text": "Moderadora: Canadá produjo 3,27 billones de dólares en 2024."},
             {"cid": "P1F6", "category": "f", "expected": "true", "voice": "B",
              "ref": "WB SP.POP.TOTL CAN 2024 = 41,262,329",
              "sources": wb_src("/country/CAN/indicator/SP.POP.TOTL?format=json&date=2024"),
              "ground_truth": "41,26M; dice 41,3M -> TRUE",
              "text": "Invitado: Canadá tenía 41,3 millones de habitantes en 2024."},
         ]},
        {"n": 6, "chart": "truncated_mex", "title": "El gráfico tramposo",
         "bullets": ["México 2023: 1,79 billones", "México 2024: 1,83 billones",
                     "El eje empieza en 1,7"], "avatar": "panel",
         "segs": [
             {"cid": "P1C4", "category": "c", "expected": "false", "voice": "A",
              "ref": "WB MEX 1,79T -> 1,83T (+2 %, no x2); eje truncado",
              "sources": wb_src("/country/MEX/indicator/NY.GDP.MKTP.CD?format=json&date=2023:2024"),
              "ground_truth": "+2 %; dice duplicado con eje truncado -> FALSE",
              "text": "Moderadora: Como ven, la economía mexicana se duplicó en un año."},
             {"cid": "P1A7", "category": "a", "expected": "true", "voice": "B",
              "ref": "WB NY.GDP.MKTP.CD MEX 2023 = 1,794,410,347,718",
              "sources": wb_src("/country/MEX/indicator/NY.GDP.MKTP.CD?format=json&date=2023:2024"),
              "ground_truth": "1,794T; dice 1,79T -> TRUE",
              "text": "Invitado: En 2023 el PIB de México fue de 1,79 billones de dólares."},
             {"cid": "P1E2", "category": "e", "expected": "true", "voice": "A",
              "ref": "WB SP.POP.TOTL ESP 2024 = 48,848,840",
              "sources": wb_src("/country/ESP/indicator/SP.POP.TOTL?format=json&date=2024"),
              "ground_truth": "48,85M; dice 48,8M, escala correcta -> TRUE",
              "text": "Moderadora: España tenía 48,8 millones de habitantes en 2024."},
             {"cid": "P1E5", "category": "e", "expected": "false", "voice": "B",
              "ref": "WB ESP 2024 = 48,848,840 (48,8M); 488M es x10",
              "sources": wb_src("/country/ESP/indicator/SP.POP.TOTL?format=json&date=2024"),
              "ground_truth": "48,8M; dice 488M (x10) -> FALSE",
              "text": "Invitado: España tenía 488 millones de habitantes en 2024."},
         ]},
        {"n": 7, "chart": None, "title": "Razones y promedios",
         "bullets": ["China frente a EE. UU.: 63,9 %", "Perú: 292000 millones",
                     "Japón bajo Alemania"], "avatar": "panel",
         "segs": [
             {"cid": "P1D4", "category": "d", "expected": "true", "voice": "A",
              "ref": "WB 2024: CHN 18,729,668,435,848 / USA 29,298,013,000,000 = 63,93 %",
              "sources": wb_src("/country/CHN;USA/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "63,93 %; dice 63,9 % -> TRUE",
              "calculation": "18729668/29298013 = 0,6393 = 63,93 %",
              "text": "Moderadora: En 2024 el PIB chino fue el 63,9 por ciento del de EE. UU."},
             {"cid": "P1D5", "category": "d", "expected": "false", "voice": "B",
              "ref": "WB 2024: JPN 4,190,008,188,359 / DEU 4,685,592,577,805 = 89,42 %",
              "sources": wb_src("/country/JPN;DEU/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "89,42 %; dice 110 % -> FALSE",
              "calculation": "4190008/4685593 = 0,8942 = 89,42 %",
              "text": "Invitado: El PIB japonés fue el 110 por ciento del alemán en 2024."},
             {"cid": "P1F7", "category": "f", "expected": "true", "voice": "A",
              "ref": "WB NY.GDP.MKTP.CD PER 2024 = 291,751,523,019",
              "sources": wb_src("/country/PER/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "291,75B; dice 292000M -> TRUE",
              "text": "Moderadora: Perú generó 292000 millones de dólares en 2024."},
             {"cid": "P1C5", "category": "c", "expected": "false", "voice": "B",
              "ref": "WB PER 2024 = 291,751,523,019 (0,29T, no 1T)",
              "sources": wb_src("/country/PER/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "0,29T; dice más de 1T, inventado -> FALSE",
              "text": "Invitado: El PIB peruano superó el billón de dólares en 2024."},
         ]},
        {"n": 8, "chart": None, "title": "Mirando a 2026",
         "bullets": ["FMI: +3,1 % mundial", "Paro EE. UU.: 4,2 %",
                     "Parados: 7,1 millones"], "avatar": "panel",
         "segs": [
             {"cid": "P1B1", "category": "b", "expected": "true", "voice": "A",
              "ref": "IMF WEO oct-2026: crecimiento mundial 3,1 % en 2026",
              "sources": imf_src("primario: WEO oct-2026; ground truth de dev.json F2D-B1"),
              "ground_truth": "3,1 % mundial 2026 -> TRUE",
              "text": "Moderadora: El FMI ve un crecimiento mundial del 3,1 por ciento en 2026."},
             {"cid": "P1B2", "category": "b", "expected": "false", "voice": "B",
              "ref": "Mismo WEO: 3,1 % mundial; 2,1 % es solo avanzadas",
              "sources": imf_src("primario, mismo WEO; refuta (3,1 % frente a 2,1 %)"),
              "ground_truth": "3,1 %; dice 2,1 % -> FALSE",
              "text": "Invitado: El FMI ve un crecimiento mundial del 2,1 por ciento en 2026."},
             {"cid": "P1B3", "category": "b", "expected": "true", "voice": "A",
              "ref": "BLS empsit sep-2026: paro 4,2 %",
              "sources": bls_src("primario: Employment Situation sep-2026; ground truth de dev.json F2D-B3"),
              "ground_truth": "4,2 % -> TRUE",
              "text": "Moderadora: El desempleo en EE. UU. fue del 4,2 por ciento en sep-2026."},
             {"cid": "P1B4", "category": "b", "expected": "false", "voice": "B",
              "ref": "BLS empsit sep-2026: 7,1M, no 8,1M",
              "sources": bls_src("primario, mismo boletín; refuta (7,1M frente a 8,1M)"),
              "ground_truth": "7,1M; dice 8,1M -> FALSE",
              "text": "Invitado: En sep-2026 había 8,1 millones de desempleados en EE. UU."},
         ]},
        {"n": 9, "chart": None, "title": "Más 2026",
         "bullets": ["Parados: 7,1 millones", "FMI 2027: +3,4 %",
                     "Chile: dólares, no euros"], "avatar": "panel",
         "segs": [
             {"cid": "P1B5", "category": "b", "expected": "true", "voice": "A",
              "ref": "BLS empsit sep-2026: 7,1 millones de desempleados",
              "sources": bls_src("primario, mismo boletín; ground truth de dev.json F2D-B5"),
              "ground_truth": "7,1M -> TRUE",
              "text": "Moderadora: En sep-2026 había 7,1 millones de desempleados en EE. UU."},
             {"cid": "P1B6", "category": "b", "expected": "false", "voice": "B",
              "ref": "Mismo WEO: 3,4 % es 2027, no 2026",
              "sources": imf_src("primario, mismo WEO; refuta (3,4 % es de 2027)"),
              "ground_truth": "3,1 % en 2026; dice 3,4 % -> FALSE",
              "text": "Invitado: El FMI ve un crecimiento mundial del 3,4 por ciento en 2026."},
             {"cid": "P1E3", "category": "e", "expected": "true", "voice": "A",
              "ref": "WB SP.POP.TOTL CHL 2024 = 19,764,771",
              "sources": wb_src("/country/CHL/indicator/SP.POP.TOTL?format=json&date=2024"),
              "ground_truth": "19,76M; dice 19,8M, escala correcta -> TRUE",
              "text": "Moderadora: Chile tenía 19,8 millones de habitantes en 2024."},
             {"cid": "P1E6", "category": "e", "expected": "false", "voice": "B",
              "ref": "WB CHL 2024 = 329,260,633,699 USD; euros != dólares",
              "sources": wb_src("/country/CHL/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "329B USD; dice euros (unidad errónea) -> FALSE",
              "text": "Invitado: El PIB chileno fue de 329000 millones de euros en 2024."},
         ]},
        {"n": 10, "chart": None, "title": "Despedida",
         "bullets": ["Colombia: 52,9 millones", "Brecha EE. UU.-China: 10,57 billones",
                     "Nigeria: 232,7 millones"], "avatar": "panel",
         "segs": [
             {"cid": "P1A8", "category": "a", "expected": "true", "voice": "A",
              "ref": "WB SP.POP.TOTL COL 2024 = 52,886,363",
              "sources": wb_src("/country/COL/indicator/SP.POP.TOTL?format=json&date=2024"),
              "ground_truth": "52,89M; dice 52,9M -> TRUE",
              "text": "Moderadora: Colombia tenía 52,9 millones de habitantes en 2024."},
             {"cid": "P1D6", "category": "d", "expected": "true", "voice": "B",
              "ref": "WB 2024: USA 29,298,013,000,000 - CHN 18,729,668,435,848 = 10,568T",
              "sources": wb_src("/country/USA;CHN/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "10,568T; dice 10,57T -> TRUE",
              "calculation": "29298013-18729668 = 10568345M = 10,568T",
              "text": "Invitado: La brecha entre EE. UU. y China fue de 10,57 billones."},
             {"cid": "P1F8", "category": "f", "expected": "false", "voice": "A",
              "ref": "WB 2024: NGA 252B < BRA 2186B; no superó a Brasil",
              "sources": wb_src("/country/NGA;BRA/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "252B < 2186B; el sorpasso no ocurrió -> FALSE",
              "text": "Moderadora: Nigeria superó a Brasil en PIB en 2024."},
             {"cid": "P1C6", "category": "c", "expected": "false", "voice": "B",
              "ref": "WB NGA 2024 = 232,679,478 (232,7M, no 300M)",
              "sources": wb_src("/country/NGA/indicator/SP.POP.TOTL?format=json&date=2024"),
              "ground_truth": "232,7M; dice 300M, inventado -> FALSE",
              "text": "Invitado: Nigeria superó los 300 millones de habitantes en 2024."},
         ]},
    ],
}

# ============ C1: clase "Geografía de la población" ========================
PIECES["C1"] = {
    "file": "clase_poblacion",
    "title": "Geografía de la población",
    "kind": "clase",
    "role": "PROFESORA",
    "slides": [
        {"n": 1, "chart": None, "title": "¿Cuántos somos?",
         "bullets": ["Mundo: 8141 millones", "China: 1409 millones",
                     "EE. UU.: 340 millones"], "avatar": "profesora",
         "segs": [
             {"cid": "C1A1", "category": "a", "expected": "true",
              "ref": "WB SP.POP.TOTL WLD 2024 = 8,140,897,523",
              "sources": wb_src("/country/WLD/indicator/SP.POP.TOTL?format=json&date=2024"),
              "ground_truth": "8140,90M; dice 8141M -> TRUE",
              "text": "En 2024 éramos 8141 millones de personas en el planeta."},
             {"cid": "C1A2", "category": "a", "expected": "true",
              "ref": "WB SP.POP.TOTL CHN 2024 = 1,408,975,000",
              "sources": wb_src("/country/CHN/indicator/SP.POP.TOTL?format=json&date=2024"),
              "ground_truth": "1408,98M; dice 1409M -> TRUE",
              "text": "China rozaba los 1409 millones de habitantes en 2024."},
             {"cid": "C1F1", "category": "f", "expected": "true",
              "ref": "WB SP.POP.TOTL USA 2024 = 340,003,797",
              "sources": wb_src("/country/USA/indicator/SP.POP.TOTL?format=json&date=2024"),
              "ground_truth": "340,00M; dice 340M -> TRUE",
              "text": "La población estadounidense llegó a 340 millones de personas."},
             {"cid": "C1C1", "category": "c", "expected": "false",
              "ref": "WB WLD 2024 = 8141M y subiendo; ni cayó ni bajó de 8000M",
              "sources": wb_src("/country/WLD/indicator/SP.POP.TOTL?format=json&date=2023:2024"),
              "ground_truth": "8141M al alza; dice caída bajo 8000M -> FALSE",
              "text": "La población mundial cayó por debajo de 8000 millones en 2024."},
         ]},
        {"n": 2, "chart": None, "title": "Asia populosa",
         "bullets": ["Indonesia: 283,5 millones", "Brasil: 212 millones",
                     "Japón: 124,0 millones"], "avatar": "profesora",
         "segs": [
             {"cid": "C1A3", "category": "a", "expected": "true",
              "ref": "WB SP.POP.TOTL IDN 2024 = 283,487,931",
              "sources": wb_src("/country/IDN/indicator/SP.POP.TOTL?format=json&date=2024"),
              "ground_truth": "283,49M; dice 283,5M -> TRUE",
              "text": "Indonesia tenía 283,5 millones de habitantes en 2024."},
             {"cid": "C1A4", "category": "a", "expected": "false",
              "ref": "WB JPN 2024 = 123,975,371 (124,0M, no 144M)",
              "sources": wb_src("/country/JPN/indicator/SP.POP.TOTL?format=json&date=2024"),
              "ground_truth": "124,0M; dice 144M, dif 16 % -> FALSE",
              "text": "Japón tenía 144 millones de habitantes en 2024."},
             {"cid": "C1F2", "category": "f", "expected": "true",
              "ref": "WB SP.POP.TOTL BRA 2024 = 211,998,573",
              "sources": wb_src("/country/BRA/indicator/SP.POP.TOTL?format=json&date=2024"),
              "ground_truth": "212,00M; dice 212M -> TRUE",
              "text": "Brasil rozaba los 212 millones de personas en 2024."},
             {"cid": "C1C2", "category": "c", "expected": "false",
              "ref": "WB CHN 1410,71M -> 1408,98M (-1,7M, no -100M)",
              "sources": wb_src("/country/CHN/indicator/SP.POP.TOTL?format=json&date=2023:2024"),
              "ground_truth": "-1,7M; dice -100M, inventado -> FALSE",
              "text": "China perdió 100 millones de habitantes en un solo año."},
         ]},
        {"n": 3, "chart": None, "title": "África y América",
         "bullets": ["Nigeria: 232,7 millones", "México: 130,9 millones",
                     "Nigeria bajo EE. UU."], "avatar": "profesora",
         "segs": [
             {"cid": "C1A5", "category": "a", "expected": "true",
              "ref": "WB SP.POP.TOTL NGA 2024 = 232,679,478",
              "sources": wb_src("/country/NGA/indicator/SP.POP.TOTL?format=json&date=2024"),
              "ground_truth": "232,68M; dice 232,7M -> TRUE",
              "text": "Nigeria reunía 232,7 millones de habitantes en 2024."},
             {"cid": "C1A6", "category": "a", "expected": "true",
              "ref": "WB SP.POP.TOTL MEX 2024 = 130,861,007",
              "sources": wb_src("/country/MEX/indicator/SP.POP.TOTL?format=json&date=2024"),
              "ground_truth": "130,86M; dice 130,9M -> TRUE",
              "text": "México sumaba 130,9 millones de habitantes en 2024."},
             {"cid": "C1F3", "category": "f", "expected": "false",
              "ref": "WB 2024: NGA 232,7M < USA 340,0M; no superó a EE. UU.",
              "sources": wb_src("/country/NGA;USA/indicator/SP.POP.TOTL?format=json&date=2024"),
              "ground_truth": "232,7 < 340,0; el sorpasso no ocurrió -> FALSE",
              "text": "Nigeria superó a Estados Unidos en población en 2024."},
             {"cid": "C1C3", "category": "c", "expected": "false",
              "ref": "WB MEX 2024 = 130,861,007 (130,9M); 260M inventado",
              "sources": wb_src("/country/MEX/indicator/SP.POP.TOTL?format=json&date=2024"),
              "ground_truth": "130,9M; dice 260M, inventado -> FALSE",
              "text": "México duplicó su población hasta 260 millones en 2024."},
         ]},
        {"n": 4, "chart": None, "title": "Europa",
         "bullets": ["Alemania: 83,5 millones", "España: 48,8 millones",
                     "Italia: 59,0 millones"], "avatar": "profesora",
         "segs": [
             {"cid": "C1A7", "category": "a", "expected": "true",
              "ref": "WB SP.POP.TOTL DEU 2024 = 83,516,593",
              "sources": wb_src("/country/DEU/indicator/SP.POP.TOTL?format=json&date=2024"),
              "ground_truth": "83,52M; dice 83,5M -> TRUE",
              "text": "Alemania tenía 83,5 millones de habitantes en 2024."},
             {"cid": "C1A8", "category": "a", "expected": "false",
              "ref": "WB FRA 2024 = 68,551,653 (68,6M, no 80M)",
              "sources": wb_src("/country/FRA/indicator/SP.POP.TOTL?format=json&date=2024"),
              "ground_truth": "68,6M; dice más de 80M -> FALSE",
              "text": "Francia superó los 80 millones de habitantes en 2024."},
             {"cid": "C1F4", "category": "f", "expected": "true",
              "ref": "WB SP.POP.TOTL ESP 2024 = 48,848,840",
              "sources": wb_src("/country/ESP/indicator/SP.POP.TOTL?format=json&date=2024"),
              "ground_truth": "48,85M; dice 48,8M -> TRUE",
              "text": "En España vivían 48,8 millones de personas en 2024."},
             {"cid": "C1E1", "category": "e", "expected": "true",
              "ref": "WB SP.POP.TOTL ITA 2024 = 58,952,704",
              "sources": wb_src("/country/ITA/indicator/SP.POP.TOTL?format=json&date=2024"),
              "ground_truth": "58,95M; dice 59,0M, escala correcta -> TRUE",
              "text": "Italia tenía 59,0 millones de habitantes en 2024."},
         ]},
        {"n": 5, "chart": "pop_change", "title": "Crecer o menguar",
         "bullets": ["Mundo: +1,0 %", "China: menos 1,7 millones",
                     "India siguió creciendo"], "avatar": "profesora",
         "segs": [
             {"cid": "C1D1", "category": "d", "expected": "true",
              "ref": "WB WLD 8,062,923,417 -> 8,140,897,523: +0,967 %",
              "sources": wb_src("/country/WLD/indicator/SP.POP.TOTL?format=json&date=2023:2024"),
              "ground_truth": "+0,967 %; dice 1,0 % (1 decimal) -> TRUE",
              "calculation": "(8140898-8062923)/8062923 = 0,00967 = 0,967 %",
              "text": "La población mundial creció un 1,0 por ciento entre 2023 y 2024."},
             {"cid": "C1D2", "category": "d", "expected": "false",
              "ref": "WB WLD: +0,967 %, no 9,7 % (x10)",
              "sources": wb_src("/country/WLD/indicator/SP.POP.TOTL?format=json&date=2023:2024"),
              "ground_truth": "+0,967 %; dice 9,7 % -> FALSE",
              "calculation": "(8140898-8062923)/8062923 = 0,00967 = 0,967 %",
              "text": "La población mundial creció un 9,7 por ciento entre 2023 y 2024."},
             {"cid": "C1D3", "category": "d", "expected": "true",
              "ref": "WB CHN 1,410,710,000 -> 1,408,975,000: -1,735M",
              "sources": wb_src("/country/CHN/indicator/SP.POP.TOTL?format=json&date=2023:2024"),
              "ground_truth": "-1,735M; dice 1,7M -> TRUE",
              "calculation": "1408975-1410710 = -1735 miles",
              "text": "China perdió 1,7 millones de habitantes entre 2023 y 2024."},
             {"cid": "C1D4", "category": "d", "expected": "false",
              "ref": "WB IND 1,438,069,596 -> 1,450,935,791 (+12,9M); no perdió",
              "sources": wb_src("/country/IND/indicator/SP.POP.TOTL?format=json&date=2023:2024"),
              "ground_truth": "+12,9M; dice que perdió -> FALSE",
              "calculation": "1450936-1438070 = +12866 miles",
              "text": "La India perdió población entre 2023 y 2024."},
         ]},
        {"n": 6, "chart": "truncated_arg", "title": "El gráfico engañoso",
         "bullets": ["Argentina 2023: 45,5 millones", "Argentina 2024: 45,7 millones",
                     "El eje empieza en 45"], "avatar": "profesora",
         "segs": [
             {"cid": "C1C4", "category": "c", "expected": "false",
              "ref": "WB ARG 45,538,401 -> 45,696,159 (+0,3 %, no x2); eje truncado",
              "sources": wb_src("/country/ARG/indicator/SP.POP.TOTL?format=json&date=2023:2024"),
              "ground_truth": "+0,3 %; dice duplicado con eje truncado -> FALSE",
              "text": "Como muestra el gráfico, la población argentina se duplicó."},
             {"cid": "C1A9", "category": "a", "expected": "true",
              "ref": "WB SP.POP.TOTL ARG 2024 = 45,696,159",
              "sources": wb_src("/country/ARG/indicator/SP.POP.TOTL?format=json&date=2024"),
              "ground_truth": "45,70M; dice 45,7M -> TRUE",
              "text": "Argentina tenía 45,7 millones de habitantes en 2024."},
             {"cid": "C1E4", "category": "e", "expected": "false",
              "ref": "WB ARG 2024 = 45,696,159 (45,7M); 4,6M es x10 menos",
              "sources": wb_src("/country/ARG/indicator/SP.POP.TOTL?format=json&date=2024"),
              "ground_truth": "45,7M; dice 4,6M (x10) -> FALSE",
              "text": "Argentina tenía 4,6 millones de habitantes en 2024."},
             {"cid": "C1F5", "category": "f", "expected": "true",
              "ref": "WB SP.POP.TOTL CAN 2024 = 41,262,329",
              "sources": wb_src("/country/CAN/indicator/SP.POP.TOTL?format=json&date=2024"),
              "ground_truth": "41,26M; dice 41,3M -> TRUE",
              "text": "Canadá contaba con 41,3 millones de habitantes en 2024."},
         ]},
        {"n": 7, "chart": None, "title": "Comparaciones",
         "bullets": ["India menos China: 42,0 millones",
                     "México menos Colombia: 78,0 millones",
                     "Media alemana-francesa: 76,0"], "avatar": "profesora",
         "segs": [
             {"cid": "C1D5", "category": "d", "expected": "true",
              "ref": "WB 2024: IND 1,450,935,791 - CHN 1,408,975,000 = 41,96M",
              "sources": wb_src("/country/IND;CHN/indicator/SP.POP.TOTL?format=json&date=2024"),
              "ground_truth": "41,96M; dice 42,0M -> TRUE",
              "calculation": "1450935791-1408975000 = 41960791",
              "text": "La India superó a China en 42,0 millones de habitantes en 2024."},
             {"cid": "C1D6", "category": "d", "expected": "false",
              "ref": "WB 2024: CHN 1409M < IND 1451M; China no superó a India",
              "sources": wb_src("/country/IND;CHN/indicator/SP.POP.TOTL?format=json&date=2024"),
              "ground_truth": "1409 < 1451; dice China +100M -> FALSE",
              "calculation": "1408975 frente a 1450936 miles",
              "text": "China superó a la India en 100 millones de habitantes en 2024."},
             {"cid": "C1D7", "category": "d", "expected": "true",
              "ref": "WB 2024: MEX 130,861,007 - COL 52,886,363 = 77,97M",
              "sources": wb_src("/country/MEX;COL/indicator/SP.POP.TOTL?format=json&date=2024"),
              "ground_truth": "77,97M; dice 78,0M -> TRUE",
              "calculation": "130861007-52886363 = 77974644",
              "text": "México superó a Colombia en 78,0 millones de habitantes en 2024."},
             {"cid": "C1D8", "category": "d", "expected": "false",
              "ref": "WB 2024: (DEU 83,516,593 + FRA 68,551,653) / 2 = 76,03M, no 86M",
              "sources": wb_src("/country/DEU;FRA/indicator/SP.POP.TOTL?format=json&date=2024"),
              "ground_truth": "76,03M; dice 86M -> FALSE",
              "calculation": "(83516593+68551653)/2 = 76034123",
              "text": "La media de Alemania y Francia en 2024 fue de 86 millones."},
         ]},
        {"n": 8, "chart": None, "title": "Escalas",
         "bullets": ["Perú: 34,2 millones", "Colombia: 52,9 millones",
                     "Cuidado con los ceros"], "avatar": "profesora",
         "segs": [
             {"cid": "C1E2", "category": "e", "expected": "true",
              "ref": "WB SP.POP.TOTL PER 2024 = 34,217,848",
              "sources": wb_src("/country/PER/indicator/SP.POP.TOTL?format=json&date=2024"),
              "ground_truth": "34,22M; dice 34,2M, escala correcta -> TRUE",
              "text": "Perú tenía 34,2 millones de habitantes en 2024."},
             {"cid": "C1E5", "category": "e", "expected": "false",
              "ref": "WB PER 2024 = 34,217,848 (34,2M); 342M es x10",
              "sources": wb_src("/country/PER/indicator/SP.POP.TOTL?format=json&date=2024"),
              "ground_truth": "34,2M; dice 342M (x10) -> FALSE",
              "text": "Perú tenía 342 millones de habitantes en 2024."},
             {"cid": "C1E3", "category": "e", "expected": "true",
              "ref": "WB SP.POP.TOTL COL 2024 = 52,886,363",
              "sources": wb_src("/country/COL/indicator/SP.POP.TOTL?format=json&date=2024"),
              "ground_truth": "52,89M; dice 52,9M, escala correcta -> TRUE",
              "text": "Colombia reunía 52,9 millones de habitantes en 2024."},
             {"cid": "C1E6", "category": "e", "expected": "false",
              "ref": "WB COL 2024 = 52,886,363 (52,9M); 529M es x10",
              "sources": wb_src("/country/COL/indicator/SP.POP.TOTL?format=json&date=2024"),
              "ground_truth": "52,9M; dice 529M (x10) -> FALSE",
              "text": "Colombia tenía 529 millones de habitantes en 2024."},
         ]},
        {"n": 9, "chart": None, "title": "El mundo hoy",
         "bullets": ["FMI: +3,1 % en 2026", "Japón: 124,0 millones",
                     "Alemania: 83,5 millones"], "avatar": "profesora",
         "segs": [
             {"cid": "C1B1", "category": "b", "expected": "true",
              "ref": "IMF WEO oct-2026: crecimiento mundial 3,1 % en 2026",
              "sources": imf_src("primario: WEO oct-2026; ground truth de dev.json F2D-B1"),
              "ground_truth": "3,1 % mundial 2026 -> TRUE",
              "text": "El FMI espera un crecimiento mundial del 3,1 por ciento en 2026."},
             {"cid": "C1B2", "category": "b", "expected": "false",
              "ref": "BLS empsit sep-2026: 4,2 % (no 3,2 %)",
              "sources": bls_src("primario, mismo boletín; refuta (4,2 % frente a 3,2 %)"),
              "ground_truth": "4,2 %; dice 3,2 % -> FALSE",
              "text": "El paro en EE. UU. bajó al 3,2 por ciento en sep-2026."},
             {"cid": "C1F6", "category": "f", "expected": "true",
              "ref": "WB SP.POP.TOTL JPN 2024 = 123,975,371",
              "sources": wb_src("/country/JPN/indicator/SP.POP.TOTL?format=json&date=2024"),
              "ground_truth": "123,98M; dice 124,0M -> TRUE",
              "text": "Japón tenía 124,0 millones de habitantes en 2024."},
             {"cid": "C1F7", "category": "f", "expected": "false",
              "ref": "WB DEU 2024 = 83,516,593 (83,5M, no 100M)",
              "sources": wb_src("/country/DEU/indicator/SP.POP.TOTL?format=json&date=2024"),
              "ground_truth": "83,5M; dice más de 100M -> FALSE",
              "text": "Alemania superó los 100 millones de habitantes en 2024."},
         ]},
        {"n": 10, "chart": None, "title": "Repaso",
         "bullets": ["Reino Unido: 69,3 millones", "Alemania por encima",
                     "Italia: 59,0 millones"], "avatar": "profesora",
         "segs": [
             {"cid": "C1A10", "category": "a", "expected": "false",
              "ref": "WB GBR 2024 = 69,281,000 (69,3M, no 79,3M)",
              "sources": wb_src("/country/GBR/indicator/SP.POP.TOTL?format=json&date=2024"),
              "ground_truth": "69,3M; dice 79,3M -> FALSE",
              "text": "El Reino Unido tenía 79,3 millones de habitantes en 2024."},
             {"cid": "C1C5", "category": "c", "expected": "false",
              "ref": "WB 2024: GBR 69,3M < DEU 83,5M; no superó a Alemania",
              "sources": wb_src("/country/GBR;DEU/indicator/SP.POP.TOTL?format=json&date=2024"),
              "ground_truth": "69,3 < 83,5; el sorpasso no ocurrió -> FALSE",
              "text": "El Reino Unido superó a Alemania en población en 2024."},
             {"cid": "C1C6", "category": "c", "expected": "false",
              "ref": "WB ITA 2024 = 58,952,704 (59,0M); 68,6M es FRANCIA (swap)",
              "sources": wb_src("/country/ITA;FRA/indicator/SP.POP.TOTL?format=json&date=2024"),
              "ground_truth": "59,0M; dice 68,6M de Francia -> FALSE",
              "text": "Italia alcanzó 68,6 millones de habitantes en 2024."},
             {"cid": "C1F8", "category": "f", "expected": "true",
              "ref": "WB SP.POP.TOTL GBR 2024 = 69,281,000",
              "sources": wb_src("/country/GBR/indicator/SP.POP.TOTL?format=json&date=2024"),
              "ground_truth": "69,28M; dice 69,3M -> TRUE",
              "text": "El Reino Unido contaba con 69,3 millones de personas en 2024."},
         ]},
    ],
}

# ============ C2: clase "Matemáticas de la economía" =======================
PIECES["C2"] = {
    "file": "clase_economia",
    "title": "Matemáticas de la economía",
    "kind": "clase",
    "role": "PROFESORA",
    "slides": [
        {"n": 1, "chart": None, "title": "El PIB en cifras",
         "bullets": ["EE. UU.: 29,3 billones", "China: 18,73 billones",
                     "Japón: 4,19 billones"], "avatar": "profesora",
         "segs": [
             {"cid": "C2A1", "category": "a", "expected": "true",
              "ref": "WB NY.GDP.MKTP.CD USA 2024 = 29,298,013,000,000",
              "sources": wb_src("/country/USA/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "29,298T; dice 29,3T -> TRUE",
              "text": "Estados Unidos produjo 29,3 billones de dólares en 2024."},
             {"cid": "C2A2", "category": "a", "expected": "false",
              "ref": "WB CHN 2024 = 18,729,668,435,848 (18,73T, no 21,7T)",
              "sources": wb_src("/country/CHN/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "18,73T; dice 21,7T -> FALSE",
              "text": "China produjo 21,7 billones de dólares en 2024."},
             {"cid": "C2F1", "category": "f", "expected": "true",
              "ref": "WB NY.GDP.MKTP.CD JPN 2024 = 4,190,008,188,359",
              "sources": wb_src("/country/JPN/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "4,190T; dice 4,19T -> TRUE",
              "text": "Japón generó 4,19 billones de dólares en 2024."},
             {"cid": "C2C1", "category": "c", "expected": "false",
              "ref": "WB JPN 2024 = 4,19T; 8,4T es el doble inventado",
              "sources": wb_src("/country/JPN/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "4,19T; dice 8,4T, inventado -> FALSE",
              "text": "El PIB de Japón se duplicó hasta 8,4 billones en 2024."},
         ]},
        {"n": 2, "chart": None, "title": "Tasas de crecimiento",
         "bullets": ["EE. UU.: +5,3 %", "India: +7,4 %", "España: +6,6 %",
                     "Japón: menos 4,4 %"], "avatar": "profesora",
         "segs": [
             {"cid": "C2D1", "category": "d", "expected": "true",
              "ref": "WB USA 27,811,517,000,000 -> 29,298,013,000,000: +5,345 %",
              "sources": wb_src("/country/USA/indicator/NY.GDP.MKTP.CD?format=json&date=2023:2024"),
              "ground_truth": "+5,345 %; dice 5,3 % -> TRUE",
              "calculation": "(29298013-27811517)/27811517 = 0,05345 = 5,345 %",
              "text": "El PIB de EE. UU. creció un 5,3 por ciento entre 2023 y 2024."},
             {"cid": "C2D2", "category": "d", "expected": "false",
              "ref": "WB IND: +7,424 %, no 12,4 %",
              "sources": wb_src("/country/IND/indicator/NY.GDP.MKTP.CD?format=json&date=2023:2024"),
              "ground_truth": "+7,424 %; dice 12,4 % -> FALSE",
              "calculation": "(3760813-3500906)/3500906 = 0,07424 = 7,424 %",
              "text": "El PIB indio creció un 12,4 por ciento entre 2023 y 2024."},
             {"cid": "C2D3", "category": "d", "expected": "true",
              "ref": "WB ESP 1,619,481,980,720 -> 1,725,671,652,742: +6,557 %",
              "sources": wb_src("/country/ESP/indicator/NY.GDP.MKTP.CD?format=json&date=2023:2024"),
              "ground_truth": "+6,557 %; dice 6,6 % -> TRUE",
              "calculation": "(1725672-1619482)/1619482 = 0,06557 = 6,557 %",
              "text": "El PIB español creció un 6,6 por ciento entre 2023 y 2024."},
             {"cid": "C2D4", "category": "d", "expected": "false",
              "ref": "WB JPN: -4,444 % (cayó); dice +3,1 %",
              "sources": wb_src("/country/JPN/indicator/NY.GDP.MKTP.CD?format=json&date=2023:2024"),
              "ground_truth": "-4,444 %; dice +3,1 % -> FALSE",
              "calculation": "(4190008-4384854)/4384854 = -0,04444 = -4,444 %",
              "text": "El PIB japonés creció un 3,1 por ciento entre 2023 y 2024."},
         ]},
        {"n": 3, "chart": None, "title": "Caídas también cuentan",
         "bullets": ["Japón: menos 4,4 %", "Brasil: menos 0,2 %",
                     "México: +2,0 %"], "avatar": "profesora",
         "segs": [
             {"cid": "C2D5", "category": "d", "expected": "true",
              "ref": "WB JPN 4,384,854,269,962 -> 4,190,008,188,359: -4,444 %",
              "sources": wb_src("/country/JPN/indicator/NY.GDP.MKTP.CD?format=json&date=2023:2024"),
              "ground_truth": "-4,444 %; dice cayó 4,4 % -> TRUE",
              "calculation": "(4190008-4384854)/4384854 = -0,04444 = -4,444 %",
              "text": "El PIB japonés cayó un 4,4 por ciento entre 2023 y 2024."},
             {"cid": "C2D6", "category": "d", "expected": "false",
              "ref": "WB BRA 2,191,131,765,685 -> 2,185,821,610,689: -0,242 %; dice +5,2 %",
              "sources": wb_src("/country/BRA/indicator/NY.GDP.MKTP.CD?format=json&date=2023:2024"),
              "ground_truth": "-0,242 %; dice +5,2 % -> FALSE",
              "calculation": "(2185822-2191132)/2191132 = -0,00242 = -0,242 %",
              "text": "El PIB brasileño creció un 5,2 por ciento entre 2023 y 2024."},
             {"cid": "C2D7", "category": "d", "expected": "true",
              "ref": "WB MEX 1,794,410,347,718 -> 1,830,489,311,089: +2,011 %",
              "sources": wb_src("/country/MEX/indicator/NY.GDP.MKTP.CD?format=json&date=2023:2024"),
              "ground_truth": "+2,011 %; dice 2,0 % -> TRUE",
              "calculation": "(1830489-1794410)/1794410 = 0,02011 = 2,011 %",
              "text": "El PIB mexicano creció un 2,0 por ciento entre 2023 y 2024."},
             {"cid": "C2A3", "category": "a", "expected": "true",
              "ref": "WB NY.GDP.MKTP.CD BRA 2024 = 2,185,821,610,689",
              "sources": wb_src("/country/BRA/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "2,186T; dice 2,19T -> TRUE",
              "text": "Brasil generó 2,19 billones de dólares en 2024."},
         ]},
        {"n": 4, "chart": "ratio_bars", "title": "Razones",
         "bullets": ["EE. UU. frente a India: 779 %", "China frente a EE. UU.: 63,9 %",
                     "Alemania: 4,69 billones"], "avatar": "profesora",
         "segs": [
             {"cid": "C2D8", "category": "d", "expected": "true",
              "ref": "WB 2024: USA 29,298,013,000,000 / IND 3,760,813,470,501 = 779,03 %",
              "sources": wb_src("/country/USA;IND/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "779,03 %; dice 779 % -> TRUE",
              "calculation": "29298013/3760813 = 7,7903 = 779,03 %",
              "text": "El PIB de EE. UU. fue el 779 por ciento del de la India en 2024."},
             {"cid": "C2D9", "category": "d", "expected": "false",
              "ref": "WB 2024: DEU / GBR = 126,79 %, no 175 %",
              "sources": wb_src("/country/DEU;GBR/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "126,79 %; dice 175 % -> FALSE",
              "calculation": "4685593/3695540 = 1,2679 = 126,79 %",
              "text": "El PIB alemán fue el 175 por ciento del británico en 2024."},
             {"cid": "C2D10", "category": "d", "expected": "true",
              "ref": "WB 2024: CHN 18,729,668,435,848 / USA 29,298,013,000,000 = 63,93 %",
              "sources": wb_src("/country/CHN;USA/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "63,93 %; dice 63,9 % -> TRUE",
              "calculation": "18729668/29298013 = 0,6393 = 63,93 %",
              "text": "El PIB chino fue el 63,9 por ciento del de EE. UU. en 2024."},
             {"cid": "C2A4", "category": "a", "expected": "false",
              "ref": "WB DEU 2024 = 4,685,592,577,805 (4,69T, no 5,69T)",
              "sources": wb_src("/country/DEU/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "4,69T; dice 5,69T -> FALSE",
              "text": "El PIB alemán sumó 5,69 billones de dólares en 2024."},
         ]},
        {"n": 5, "chart": None, "title": "Promedios",
         "bullets": ["Media España-Italia: 2,05 billones",
                     "Corea del Sur: 1,88 billones", "Canadá: 2,27 billones"],
         "avatar": "profesora",
         "segs": [
             {"cid": "C2D11", "category": "d", "expected": "true",
              "ref": "WB 2024: (ESP 1,725,671,652,742 + ITA 2,383,435,562,458) / 2 = 2,0546T",
              "sources": wb_src("/country/ESP;ITA/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "2,0546T; dice 2,05T -> TRUE",
              "calculation": "(1725672+2383436)/2 = 2054554M = 2,0546T",
              "text": "El PIB medio de España e Italia en 2024 fue de 2,05 billones."},
             {"cid": "C2D12", "category": "d", "expected": "false",
              "ref": "WB 2024: (KOR 1,875,388,209,407 + CAN 2,270,076,189,683) / 2 = 2,07T",
              "sources": wb_src("/country/KOR;CAN/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "2,07T; dice 2,57T -> FALSE",
              "calculation": "(1875388+2270076)/2 = 2072732M = 2,0727T",
              "text": "El PIB medio de Corea del Sur y Canadá fue de 2,57 billones."},
             {"cid": "C2A5", "category": "a", "expected": "true",
              "ref": "WB NY.GDP.MKTP.CD KOR 2024 = 1,875,388,209,407",
              "sources": wb_src("/country/KOR/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "1,875T; dice 1,88T -> TRUE",
              "text": "Corea del Sur produjo 1,88 billones de dólares en 2024."},
             {"cid": "C2A6", "category": "a", "expected": "false",
              "ref": "WB CAN 2024 = 2,270,076,189,683 (2,27T, no 3,27T)",
              "sources": wb_src("/country/CAN/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "2,27T; dice 3,27T -> FALSE",
              "text": "El PIB canadiense sumó 3,27 billones de dólares en 2024."},
         ]},
        {"n": 6, "chart": "truncated_esp", "title": "La trampa del eje",
         "bullets": ["España 2023: 1,62 billones", "España 2024: 1,73 billones",
                     "Italia por encima de España"], "avatar": "profesora",
         "segs": [
             {"cid": "C2C2", "category": "c", "expected": "false",
              "ref": "WB ESP 1,62T -> 1,73T (+6,6 %, no x2); eje truncado",
              "sources": wb_src("/country/ESP/indicator/NY.GDP.MKTP.CD?format=json&date=2023:2024"),
              "ground_truth": "+6,6 %; dice duplicado con eje truncado -> FALSE",
              "text": "Como muestra el gráfico, la economía española se duplicó."},
             {"cid": "C2E1", "category": "e", "expected": "true",
              "ref": "WB NY.GDP.MKTP.CD ESP 2024 = 1,725,671,652,742",
              "sources": wb_src("/country/ESP/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "1,726T; dice 1,73T, escala correcta -> TRUE",
              "text": "España produjo 1,73 billones de dólares en 2024."},
             {"cid": "C2E4", "category": "e", "expected": "false",
              "ref": "WB ESP 2024 = 1,73 BILLONES; 173000M es x10 menos",
              "sources": wb_src("/country/ESP/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "1,73T; dice 173000M (x10) -> FALSE",
              "text": "España produjo 173000 millones de dólares en 2024."},
             {"cid": "C2F2", "category": "f", "expected": "false",
              "ref": "WB 2024: ESP 1,73T < ITA 2,38T; no superó a Italia",
              "sources": wb_src("/country/ESP;ITA/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "1,73 < 2,38; el sorpasso no ocurrió -> FALSE",
              "text": "España superó a Italia en PIB en 2024."},
         ]},
        {"n": 7, "chart": None, "title": "Miles de millones",
         "bullets": ["México: 1,83 billones", "Indonesia: 1,40 billones",
                     "Mil millones no es un billón"], "avatar": "profesora",
         "segs": [
             {"cid": "C2E2", "category": "e", "expected": "true",
              "ref": "WB NY.GDP.MKTP.CD MEX 2024 = 1,830,489,311,089",
              "sources": wb_src("/country/MEX/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "1,830T; dice 1,83T, escala correcta -> TRUE",
              "text": "México generó 1,83 billones de dólares en 2024."},
             {"cid": "C2E5", "category": "e", "expected": "false",
              "ref": "WB MEX 2024 = 1,83 BILLONES; 1830M es x1000 menos",
              "sources": wb_src("/country/MEX/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "1,83T; dice 1830M (x1000) -> FALSE",
              "text": "La economía mexicana produjo apenas 1830 millones de dólares."},
             {"cid": "C2E3", "category": "e", "expected": "true",
              "ref": "WB NY.GDP.MKTP.CD IDN 2024 = 1,396,301,788,462",
              "sources": wb_src("/country/IDN/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "1,396T; dice 1,40T, escala correcta -> TRUE",
              "text": "Indonesia generó 1,40 billones de dólares en 2024."},
             {"cid": "C2E6", "category": "e", "expected": "false",
              "ref": "WB IDN 2024 = 1,40 BILLONES; 14000M es x100 menos",
              "sources": wb_src("/country/IDN/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "1,40T; dice 14000M (x100) -> FALSE",
              "text": "Indonesia generó 14000 millones de dólares en 2024."},
         ]},
        {"n": 8, "chart": None, "title": "Unidades",
         "bullets": ["Canadá: 2,27 billones de dólares", "Argentina: 0,64 billones",
                     "Nigeria: 252000 millones"], "avatar": "profesora",
         "segs": [
             {"cid": "C2E7", "category": "e", "expected": "true",
              "ref": "WB NY.GDP.MKTP.CD CAN 2024 = 2,270,076,189,683",
              "sources": wb_src("/country/CAN/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "2,270T USD; unidad correcta -> TRUE",
              "text": "Canadá produjo 2,27 billones de dólares en 2024."},
             {"cid": "C2E8", "category": "e", "expected": "false",
              "ref": "WB CAN serie en USD; euros != dólares",
              "sources": wb_src("/country/CAN/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "2,27T USD; dice euros (unidad errónea) -> FALSE",
              "text": "El PIB canadiense fue de 2,27 billones de euros en 2024."},
             {"cid": "C2C3", "category": "c", "expected": "false",
              "ref": "WB ARG 2024 = 638,365,455,340 (0,64T); 1,28T inventado",
              "sources": wb_src("/country/ARG/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "0,64T; dice 1,28T, inventado -> FALSE",
              "text": "El PIB argentino llegó a 1,28 billones de dólares en 2024."},
             {"cid": "C2C4", "category": "c", "expected": "false",
              "ref": "WB NGA 2024 = 252,261,880,140 (252B y cayendo); 500B inventado",
              "sources": wb_src("/country/NGA/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "252B a la baja; dice 500B -> FALSE",
              "text": "El PIB de Nigeria alcanzó 500000 millones de dólares en 2024."},
         ]},
        {"n": 9, "chart": None, "title": "Actualidad",
         "bullets": ["FMI: +3,1 % en 2026", "Francia: 3,16 billones",
                     "Alemania por encima"], "avatar": "profesora",
         "segs": [
             {"cid": "C2B1", "category": "b", "expected": "true",
              "ref": "IMF WEO oct-2026: crecimiento mundial 3,1 % en 2026",
              "sources": imf_src("primario: WEO oct-2026; ground truth de dev.json F2D-B1"),
              "ground_truth": "3,1 % mundial 2026 -> TRUE",
              "text": "El FMI anticipa un crecimiento mundial del 3,1 por ciento en 2026."},
             {"cid": "C2B2", "category": "b", "expected": "false",
              "ref": "BLS empsit sep-2026: 7,1M, no 8,1M",
              "sources": bls_src("primario, mismo boletín; refuta (7,1M frente a 8,1M)"),
              "ground_truth": "7,1M; dice 8,1M -> FALSE",
              "text": "En sep-2026 EE. UU. tenía 8,1 millones de desempleados."},
             {"cid": "C2F3", "category": "f", "expected": "true",
              "ref": "WB NY.GDP.MKTP.CD FRA 2024 = 3,160,442,622,465",
              "sources": wb_src("/country/FRA/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "3,160T; dice 3,16T -> TRUE",
              "text": "Francia produjo 3,16 billones de dólares en 2024."},
             {"cid": "C2F4", "category": "f", "expected": "false",
              "ref": "WB 2024: FRA 3,16T < DEU 4,69T; no superó a Alemania",
              "sources": wb_src("/country/FRA;DEU/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "3,16 < 4,69; el sorpasso no ocurrió -> FALSE",
              "text": "Francia superó a Alemania en PIB en 2024."},
         ]},
        {"n": 10, "chart": None, "title": "Examen sorpresa",
         "bullets": ["Colombia: 420000 millones", "Reino Unido: 3,70 billones",
                     "Alemania: 4,69 billones"], "avatar": "profesora",
         "segs": [
             {"cid": "C2C5", "category": "c", "expected": "false",
              "ref": "WB COL 2024 = 420,504,033,143 (420B); 840B es 2x inventado",
              "sources": wb_src("/country/COL/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "420B; dice 840B, inventado -> FALSE",
              "text": "El PIB de Colombia se duplicó hasta 840000 millones de dólares."},
             {"cid": "C2C6", "category": "c", "expected": "false",
              "ref": "WB 2024: GBR 3,70T < DEU 4,69T; 4,70T y sorpasso inventados",
              "sources": wb_src("/country/GBR;DEU/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "3,70 < 4,69; cifra y sorpasso inventados -> FALSE",
              "text": "El Reino Unido produjo 4,70 billones, superando a Alemania."},
             {"cid": "C2F5", "category": "f", "expected": "true",
              "ref": "WB NY.GDP.MKTP.CD DEU 2024 = 4,685,592,577,805",
              "sources": wb_src("/country/DEU/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "4,686T; dice 4,69T -> TRUE",
              "text": "Alemania produjo 4,69 billones de dólares en 2024."},
             {"cid": "C2F6", "category": "f", "expected": "false",
              "ref": "WB 2024: ITA 2,38T < FRA 3,16T; no superó a Francia",
              "sources": wb_src("/country/ITA;FRA/indicator/NY.GDP.MKTP.CD?format=json&date=2024"),
              "ground_truth": "2,38 < 3,16; el sorpasso no ocurrió -> FALSE",
              "text": "Italia superó a Francia en PIB en 2024."},
         ]},
    ],
}

# ============ E1: earnings "Luminara del Norte" (empresa FICTICIA) =========
# Todos expected=coherent-internal: cifras inventadas pero aritméticamente
# coherentes entre sí. Verificación = cálculo interno, no WB.
def _int(ref):
    return {"sources": [], "ref": "interno (empresa ficticia): " + ref}


PIECES["E1"] = {
    "file": "earnings_luminara",
    "title": "Luminara del Norte: resultados Q3 2026 (ficticia)",
    "kind": "earnings",
    "role": "DIRECTORA FINANCIERA",
    "slides": [
        {"n": 1, "chart": None, "title": "Luminara del Norte (ficticia)",
         "bullets": ["Empresa ficticia de demostración", "Sede ficticia: Bilbao",
                     "Resultados Q3 2026"], "avatar": "financiera",
         "segs": [
             {"cid": "E1F1", "category": "f", "expected": "coherent-internal",
              "ref": "ficticia por construcción (esta demo)",
              "sources": [],
              "ground_truth": "declarado ficticio en guion y slides -> COHERENT",
              "text": "Luminara del Norte es una empresa ficticia creada para esta demo."},
             {"cid": "E1F2", "category": "f", "expected": "coherent-internal",
              "ref": "ficticia por construcción (esta demo)",
              "sources": [],
              "ground_truth": "sede declarada ficticia -> COHERENT",
              "text": "La sede ficticia de Luminara está en Bilbao."},
             {"cid": "E1F3", "category": "f", "expected": "coherent-internal",
              "ref": "ficticia por construcción (esta demo)",
              "sources": [],
              "ground_truth": "3 plantas declaradas -> COHERENT",
              "text": "Luminara opera 3 plantas ficticias de energía solar."},
             {"cid": "E1F4", "category": "f", "expected": "coherent-internal",
              "ref": "marco temporal de la demo",
              "sources": [],
              "ground_truth": "Q3 2026 declarado -> COHERENT",
              "text": "Presentamos los resultados del tercer trimestre de 2026."},
         ]},
        {"n": 2, "chart": None, "title": "Ingresos récord",
         "bullets": ["Q3: 847 millones de euros", "Más 12,4 % interanual",
                     "Residencial: 512; empresas: 335"], "avatar": "financiera",
         "segs": [
             {"cid": "E1F5", "category": "f", "expected": "coherent-internal",
              "ref": "interno: cifra base Q3",
              "sources": [],
              "ground_truth": "847M base declarada -> COHERENT",
              "text": "Los ingresos del tercer trimestre fueron de 847 millones de euros."},
             {"cid": "E1D1", "category": "d", "expected": "coherent-internal",
              "ref": "interno: 847 / 753,6 - 1 = 12,393 %",
              "sources": [],
              "ground_truth": "12,393 %; dice 12,4 % -> COHERENT",
              "calculation": "847/753,6 - 1 = 0,12393 = 12,393 %",
              "text": "Crecieron un 12,4 por ciento frente a 753,6 millones de hace un año."},
             {"cid": "E1F6", "category": "f", "expected": "coherent-internal",
              "ref": "interno: cifra base segmento",
              "sources": [],
              "ground_truth": "512M base declarada -> COHERENT",
              "text": "El segmento residencial aportó 512 millones de euros."},
             {"cid": "E1F7", "category": "f", "expected": "coherent-internal",
              "ref": "interno: cifra base segmento",
              "sources": [],
              "ground_truth": "335M base declarada -> COHERENT",
              "text": "El segmento de empresas aportó 335 millones de euros."},
         ]},
        {"n": 3, "chart": None, "title": "Rentabilidad",
         "bullets": ["EBITDA: 201,5 millones (23,8 %)",
                     "Neto: 96,3 millones (11,4 %)", "Sobre 847 millones"],
         "avatar": "financiera",
         "segs": [
             {"cid": "E1F8", "category": "f", "expected": "coherent-internal",
              "ref": "interno: cifra base Q3",
              "sources": [],
              "ground_truth": "201,5M base declarada -> COHERENT",
              "text": "El EBITDA del trimestre fue de 201,5 millones de euros."},
             {"cid": "E1D2", "category": "d", "expected": "coherent-internal",
              "ref": "interno: 201,5 / 847 = 23,792 %",
              "sources": [],
              "ground_truth": "23,792 %; dice 23,8 % -> COHERENT",
              "calculation": "201,5/847 = 0,23792 = 23,792 %",
              "text": "El margen EBITDA fue del 23,8 por ciento sobre 847 millones."},
             {"cid": "E1F9", "category": "f", "expected": "coherent-internal",
              "ref": "interno: cifra base Q3",
              "sources": [],
              "ground_truth": "96,3M base declarada -> COHERENT",
              "text": "El beneficio neto del trimestre fue de 96,3 millones de euros."},
             {"cid": "E1D3", "category": "d", "expected": "coherent-internal",
              "ref": "interno: 96,3 / 847 = 11,370 %",
              "sources": [],
              "ground_truth": "11,370 %; dice 11,4 % -> COHERENT",
              "calculation": "96,3/847 = 0,11370 = 11,370 %",
              "text": "El margen neto fue del 11,4 por ciento sobre 847 millones."},
         ]},
        {"n": 4, "chart": None, "title": "Nueve meses",
         "bullets": ["Ingresos 9M: 2410 millones", "Neto 9M: 268,0 millones",
                     "Margen 9M: 11,1 %"], "avatar": "financiera",
         "segs": [
             {"cid": "E1F10", "category": "f", "expected": "coherent-internal",
              "ref": "interno: cifra base 9M",
              "sources": [],
              "ground_truth": "2410M base declarada -> COHERENT",
              "text": "Los ingresos de nueve meses sumaron 2410 millones de euros."},
             {"cid": "E1D4", "category": "d", "expected": "coherent-internal",
              "ref": "interno: 762 + 801 + 847 = 2410",
              "sources": [],
              "ground_truth": "762+801+847 = 2410 exacto -> COHERENT",
              "calculation": "762 + 801 + 847 = 2410",
              "text": "Son 762 del primero, 801 del segundo y 847 del tercer trimestre."},
             {"cid": "E1F11", "category": "f", "expected": "coherent-internal",
              "ref": "interno: cifra base 9M",
              "sources": [],
              "ground_truth": "268,0M base declarada -> COHERENT",
              "text": "El beneficio de nueve meses fue de 268,0 millones de euros."},
             {"cid": "E1D5", "category": "d", "expected": "coherent-internal",
              "ref": "interno: 268 / 2410 = 11,120 %",
              "sources": [],
              "ground_truth": "11,120 %; dice 11,1 % -> COHERENT",
              "calculation": "268/2410 = 0,11120 = 11,120 %",
              "text": "El margen de nueve meses fue del 11,1 por ciento."},
         ]},
        {"n": 5, "chart": "earn_mix", "title": "Segmentos",
         "bullets": ["Residencial: 512 (60,5 %)", "Empresas: 335 (39,5 %)",
                     "Suma: 847 millones"], "avatar": "financiera",
         "segs": [
             {"cid": "E1D6", "category": "d", "expected": "coherent-internal",
              "ref": "interno: 512 + 335 = 847",
              "sources": [],
              "ground_truth": "512+335 = 847 exacto -> COHERENT",
              "calculation": "512 + 335 = 847",
              "text": "Ambos segmentos suman 847 millones, el total del trimestre."},
             {"cid": "E1D7", "category": "d", "expected": "coherent-internal",
              "ref": "interno: 512 / 847 = 60,449 %",
              "sources": [],
              "ground_truth": "60,449 %; dice 60,5 % -> COHERENT",
              "calculation": "512/847 = 0,60449 = 60,449 %",
              "text": "El segmento residencial pesó el 60,5 por ciento de los ingresos."},
             {"cid": "E1D8", "category": "d", "expected": "coherent-internal",
              "ref": "interno: 335 / 847 = 39,551 %",
              "sources": [],
              "ground_truth": "39,551 %; dice 39,5 % -> COHERENT",
              "calculation": "335/847 = 0,39551 = 39,551 %",
              "text": "El segmento de empresas pesó el 39,5 por ciento de los ingresos."},
             {"cid": "E1F12", "category": "f", "expected": "coherent-internal",
              "ref": "interno: 96,3M neto y 1840 empleados (bases E1F9/E1F14)",
              "sources": [],
              "ground_truth": "repite bases declaradas -> COHERENT",
              "text": "La empresa ganó 96,3 millones con 1840 empleados."},
         ]},
        {"n": 6, "chart": None, "title": "Clientes y equipo",
         "bullets": ["Clientes: 2,34 millones (+8,3 %)",
                     "Plantilla: 1840 (+6,4 %)", "Base: 2,16 y 1730"],
         "avatar": "financiera",
         "segs": [
             {"cid": "E1F13", "category": "f", "expected": "coherent-internal",
              "ref": "interno: cifra base",
              "sources": [],
              "ground_truth": "2,34M base declarada -> COHERENT",
              "text": "Luminara tiene 2,34 millones de clientes."},
             {"cid": "E1D9", "category": "d", "expected": "coherent-internal",
              "ref": "interno: 2,34 / 2,16 - 1 = 8,333 %",
              "sources": [],
              "ground_truth": "8,333 %; dice 8,3 % -> COHERENT",
              "calculation": "2,34/2,16 - 1 = 0,08333 = 8,333 %",
              "text": "Los clientes crecieron un 8,3 por ciento desde 2,16 millones."},
             {"cid": "E1F14", "category": "f", "expected": "coherent-internal",
              "ref": "interno: cifra base",
              "sources": [],
              "ground_truth": "1840 base declarada -> COHERENT",
              "text": "La plantilla es de 1840 personas."},
             {"cid": "E1D10", "category": "d", "expected": "coherent-internal",
              "ref": "interno: 1840 / 1730 - 1 = 6,358 %",
              "sources": [],
              "ground_truth": "6,358 %; dice 6,4 % -> COHERENT",
              "calculation": "1840/1730 - 1 = 0,06358 = 6,358 %",
              "text": "La plantilla creció un 6,4 por ciento desde 1730 personas."},
         ]},
        {"n": 7, "chart": "earn_debt", "title": "Deuda y caja",
         "bullets": ["Deuda: 312 millones (menos 5,2 %)", "Flujo: 143 millones",
                     "Capex: 88 millones"], "avatar": "financiera",
         "segs": [
             {"cid": "E1F15", "category": "f", "expected": "coherent-internal",
              "ref": "interno: cifra base",
              "sources": [],
              "ground_truth": "312M base declarada -> COHERENT",
              "text": "La deuda neta es de 312 millones de euros."},
             {"cid": "E1D11", "category": "d", "expected": "coherent-internal",
              "ref": "interno: 312 / 329,1 - 1 = -5,196 %",
              "sources": [],
              "ground_truth": "-5,196 %; dice menos 5,2 % -> COHERENT",
              "calculation": "312/329,1 - 1 = -0,05196 = -5,196 %",
              "text": "La deuda cayó un 5,2 por ciento desde 329,1 millones."},
             {"cid": "E1F16", "category": "f", "expected": "coherent-internal",
              "ref": "interno: cifra base",
              "sources": [],
              "ground_truth": "143M base declarada -> COHERENT",
              "text": "El flujo de caja del trimestre fue de 143 millones."},
             {"cid": "E1F17", "category": "f", "expected": "coherent-internal",
              "ref": "interno: cifra base",
              "sources": [],
              "ground_truth": "88M base declarada -> COHERENT",
              "text": "La inversión del trimestre fue de 88 millones de euros."},
         ]},
        {"n": 8, "chart": None, "title": "Accionista",
         "bullets": ["BPA: 0,80 euros", "Dividendo: 0,42 euros (+16,7 %)",
                     "Capex sobre ventas: 10,4 %"], "avatar": "financiera",
         "segs": [
             {"cid": "E1D12", "category": "d", "expected": "coherent-internal",
              "ref": "interno: 96,3 / 120 = 0,8025",
              "sources": [],
              "ground_truth": "0,8025; dice 0,80 -> COHERENT",
              "calculation": "96,3/120 = 0,8025",
              "text": "El beneficio por acción fue de 0,80 euros, con 120 millones."},
             {"cid": "E1F18", "category": "f", "expected": "coherent-internal",
              "ref": "interno: cifra base",
              "sources": [],
              "ground_truth": "0,42 base declarada -> COHERENT",
              "text": "El dividendo trimestral es de 0,42 euros por acción."},
             {"cid": "E1D13", "category": "d", "expected": "coherent-internal",
              "ref": "interno: 0,42 / 0,36 - 1 = 16,667 %",
              "sources": [],
              "ground_truth": "16,667 %; dice 16,7 % -> COHERENT",
              "calculation": "0,42/0,36 - 1 = 0,16667 = 16,667 %",
              "text": "El dividendo subió un 16,7 por ciento desde 0,36 euros."},
             {"cid": "E1D14", "category": "d", "expected": "coherent-internal",
              "ref": "interno: 88 / 847 = 10,393 %",
              "sources": [],
              "ground_truth": "10,393 %; dice 10,4 % -> COHERENT",
              "calculation": "88/847 = 0,10393 = 10,393 %",
              "text": "La inversión de 88 millones fue el 10,4 por ciento de los ingresos."},
         ]},
        {"n": 9, "chart": "earn_guide", "title": "Guía anual",
         "bullets": ["Ventas guía: 3250 millones (+11,8 %)",
                     "Neto guía: 357,5 millones", "Payout: 52,3 %"],
         "avatar": "financiera",
         "segs": [
             {"cid": "E1F19", "category": "f", "expected": "coherent-internal",
              "ref": "interno: cifra guía",
              "sources": [],
              "ground_truth": "3250M guía declarada -> COHERENT",
              "text": "La guía de ingresos anuales es de 3250 millones de euros."},
             {"cid": "E1D15", "category": "d", "expected": "coherent-internal",
              "ref": "interno: 3250 / 2907 - 1 = 11,797 %",
              "sources": [],
              "ground_truth": "11,797 %; dice 11,8 % -> COHERENT",
              "calculation": "3250/2907 - 1 = 0,11797 = 11,797 %",
              "text": "La guía implica crecer un 11,8 por ciento sobre 2907 millones."},
             {"cid": "E1D16", "category": "d", "expected": "coherent-internal",
              "ref": "interno: 3250 x 0,11 = 357,5",
              "sources": [],
              "ground_truth": "357,5 exacto -> COHERENT",
              "calculation": "3250 x 0,11 = 357,5",
              "text": "Con un margen del 11 por ciento, el neto anual sería de 357,5."},
             {"cid": "E1D17", "category": "d", "expected": "coherent-internal",
              "ref": "interno: 0,42 x 120 = 50,4; 50,4 / 96,3 = 52,336 %",
              "sources": [],
              "ground_truth": "52,336 %; dice 52,3 % -> COHERENT",
              "calculation": "0,42 x 120 = 50,4; 50,4/96,3 = 0,52336 = 52,336 %",
              "text": "El dividendo total de 50,4 millones es el 52,3 por ciento del neto."},
         ]},
        {"n": 10, "chart": None, "title": "Cierre",
         "bullets": ["9M: 81,2 más 90,5 más 96,3", "Empresa ficticia",
                     "Gracias por asistir"], "avatar": "financiera",
         "segs": [
             {"cid": "E1D18", "category": "d", "expected": "coherent-internal",
              "ref": "interno: 81,2 + 90,5 + 96,3 = 268,0",
              "sources": [],
              "ground_truth": "81,2+90,5+96,3 = 268,0 exacto -> COHERENT",
              "calculation": "81,2 + 90,5 + 96,3 = 268,0",
              "text": "Los 268,0 millones son 81,2 más 90,5 más 96,3 por trimestre."},
             {"cid": "E1F20", "category": "f", "expected": "coherent-internal",
              "ref": "ficticia por construcción (esta demo)",
              "sources": [],
              "ground_truth": "repetición del disclaimer -> COHERENT",
              "text": "Repetimos: Luminara del Norte es una empresa ficticia."},
             {"cid": "E1F21", "category": "f", "expected": "coherent-internal",
              "ref": "marco ficticio de la demo",
              "sources": [],
              "ground_truth": "fecha ficticia declarada -> COHERENT",
              "text": "La próxima llamada ficticia será en febrero de 2027."},
             {"cid": "E1F22", "category": "f", "expected": "coherent-internal",
              "ref": "cierre de la demo",
              "sources": [],
              "ground_truth": "cierre declarado -> COHERENT",
              "text": "Gracias por seguir esta demostración sintética."},
         ]},
    ],
}


# ----------------------------------------------------------------------------
# Charts (matplotlib; honestos + deliberadamente engañosos documentados).
# ----------------------------------------------------------------------------
def _save(fig, name):
    import matplotlib.pyplot as plt
    os.makedirs(CHARTS, exist_ok=True)
    p = os.path.join(CHARTS, f"{name}.png")
    fig.tight_layout()
    fig.savefig(p, dpi=100)
    plt.close(fig)
    return p


def make_charts():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out = {}

    def bars(name, labels, vals, colors, title, ylabel, ylim=None):
        fig, ax = plt.subplots(figsize=(6.4, 4.0))
        bs = ax.bar(labels, vals, color=colors)
        if ylim:
            ax.set_ylim(*ylim)
        ax.set_ylabel(ylabel)
        ax.set_title(title)
        for b, v in zip(bs, vals):
            ax.text(b.get_x() + b.get_width() / 2,
                    v + (max(vals) - min(0, min(vals))) * 0.02,
                    f"{v}", ha="center", fontsize=11)
        out[name] = _save(fig, f"chart_{name}")

    # Honestos.
    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    bs = ax.bar(["EE. UU.", "España", "India", "Japón"],
                [5.3, 6.6, 7.4, -4.4],
                color=["#22c55e", "#22c55e", "#22c55e", "#ef4444"])
    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_ylabel("Crecimiento PIB 2023-2024 (%)")
    ax.set_title("Crecimiento del PIB (honesto)")
    for b, v in zip(bs, [5.3, 6.6, 7.4, -4.4]):
        ax.text(b.get_x() + b.get_width() / 2, v + (0.2 if v > 0 else -0.5),
                f"{v}%", ha="center", fontsize=11)
    out["growth_bars"] = _save(fig, "chart_growth_bars")

    bars("life_bars", ["Japón", "Suiza", "España", "EE. UU.", "Nigeria"],
         [84.0, 84.4, 83.9, 78.9, 54.6],
         ["#38bdf8"] * 5, "Esperanza de vida 2024 (años, honesto)", "Años")

    bars("mort_contrast", ["Nigeria", "Mundo", "Japón"], [115.6, 37.4, 2.4],
         ["#ef4444", "#f5b301", "#22c55e"],
         "Mortalidad <5 años 2024 por 1000 (honesto: dice 37,4)",
         "Por 1000 nacidos")

    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    bs = ax.bar(["Mundo", "India", "Argentina", "China"],
                [0.97, 0.89, 0.35, -0.12],
                color=["#22c55e", "#22c55e", "#22c55e", "#ef4444"])
    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_ylabel("Cambio población 2023-2024 (%)")
    ax.set_title("Cambio de población (honesto)")
    for b, v in zip(bs, [0.97, 0.89, 0.35, -0.12]):
        ax.text(b.get_x() + b.get_width() / 2, v + (0.03 if v > 0 else -0.08),
                f"{v}%", ha="center", fontsize=11)
    out["pop_change"] = _save(fig, "chart_pop_change")

    bars("ratio_bars", ["EE. UU.", "China", "Alemania", "R. Unido"],
         [29.3, 18.73, 4.69, 3.70], ["#38bdf8"] * 4,
         "PIB 2024 en billones de dólares (honesto)", "Billones USD")

    bars("earn_mix", ["Residencial", "Empresas"], [512, 335],
         ["#38bdf8", "#f5b301"],
         "Luminara Q3 2026: ingresos por segmento M€ (ficticia)", "Millones €")
    bars("earn_debt", ["Deuda anterior", "Deuda actual"], [329.1, 312],
         ["#94a3b8", "#22c55e"],
         "Luminara: deuda neta M€ (ficticia)", "Millones €")
    bars("earn_guide", ["2025", "Guía 2026"], [2907, 3250],
         ["#94a3b8", "#38bdf8"],
         "Luminara: ingresos anuales M€ (ficticia)", "Millones €")

    # Engañosos (documentados como props deliberados).
    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    ax.bar(["2023", "2024"], [27.8, 29.3], color=["#94a3b8", "#f5b301"])
    ax.set_ylim(27.0, 30.0)
    ax.set_ylabel("Billones USD (eje empieza en 27)")
    ax.set_title("EE. UU.: ¿se duplicó? (!) eje truncado")
    for x, v in (("2023", 27.8), ("2024", 29.3)):
        ax.text(x, v + 0.03, f"{v}", ha="center", fontsize=11)
    out["truncated_usa"] = _save(fig, "chart_truncated_usa")

    fig, (ax_top, ax_bot) = plt.subplots(
        2, 1, figsize=(6.4, 4.2), sharex=True,
        gridspec_kw={"hspace": 0.08, "height_ratios": [1, 2]})
    ax_bot.bar([0], [3.76], color="#f5b301")
    ax_bot.set_xlim(-0.6, 1.6)
    ax_bot.set_xticks([0, 1])
    ax_bot.set_xticklabels(["India", "EE. UU."])
    ax_top.bar([1], [1.3], bottom=[28.0], color="#94a3b8")
    ax_top.set_xlim(-0.6, 1.6)
    ax_top.set_ylim(27.5, 30.0)
    ax_bot.set_ylim(0, 4.2)
    ax_top.spines["bottom"].set_visible(False)
    ax_bot.spines["top"].set_visible(False)
    ax_top.tick_params(bottom=False, labelbottom=False)
    d = 0.015
    kw = dict(transform=ax_top.transAxes, color="k", clip_on=False)
    ax_top.plot((-d, +d), (-d, +d), **kw)
    ax_top.plot((1 - d, 1 + d), (-d, +d), **kw)
    kw.update(transform=ax_bot.transAxes)
    ax_bot.plot((-d, +d), (1 - d, 1 + d), **kw)
    ax_bot.plot((1 - d, 1 + d), (1 - d, 1 + d), **kw)
    fig.suptitle("PIB 2024: India frente a EE. UU. (¡escala rota!)")
    ax_bot.set_ylabel("Billones USD (¡escala rota!)")
    out["broken_ind_usa"] = _save(fig, "chart_broken_ind_usa")

    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    ax.bar(["2022", "2024"], [54.1, 54.6], color=["#94a3b8", "#f5b301"])
    ax.set_ylim(54.0, 55.0)
    ax.set_ylabel("Años (eje empieza en 54)")
    ax.set_title("Nigeria: ¿vida duplicada? (!) eje truncado")
    for x, v in (("2022", 54.1), ("2024", 54.6)):
        ax.text(x, v + 0.01, f"{v}", ha="center", fontsize=11)
    out["truncated_life"] = _save(fig, "chart_truncated_life")

    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    ax.bar(["2023", "2024"], [1.79, 1.83], color=["#94a3b8", "#f5b301"])
    ax.set_ylim(1.7, 1.9)
    ax.set_ylabel("Billones USD (eje empieza en 1,7)")
    ax.set_title("México: ¿se duplicó? (!) eje truncado")
    for x, v in (("2023", 1.79), ("2024", 1.83)):
        ax.text(x, v + 0.003, f"{v}", ha="center", fontsize=11)
    out["truncated_mex"] = _save(fig, "chart_truncated_mex")

    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    ax.bar(["2023", "2024"], [45.5, 45.7], color=["#94a3b8", "#f5b301"])
    ax.set_ylim(45.0, 46.0)
    ax.set_ylabel("Millones (eje empieza en 45)")
    ax.set_title("Argentina: ¿se duplicó? (!) eje truncado")
    for x, v in (("2023", 45.5), ("2024", 45.7)):
        ax.text(x, v + 0.01, f"{v}", ha="center", fontsize=11)
    out["truncated_arg"] = _save(fig, "chart_truncated_arg")

    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    ax.bar(["2023", "2024"], [1.62, 1.73], color=["#94a3b8", "#f5b301"])
    ax.set_ylim(1.55, 1.8)
    ax.set_ylabel("Billones USD (eje empieza en 1,55)")
    ax.set_title("España: ¿se duplicó? (!) eje truncado")
    for x, v in (("2023", 1.62), ("2024", 1.73)):
        ax.text(x, v + 0.004, f"{v}", ha="center", fontsize=11)
    out["truncated_esp"] = _save(fig, "chart_truncated_esp")

    return out


# ----------------------------------------------------------------------------
# Slides 1920x1080: rótulos + badge "Synthetic demo" quemado + presentador
# abstracto dibujado por código (formas geométricas, sin caras ni personas).
# ----------------------------------------------------------------------------
def _draw_avatar(fig, kind):
    """Presentador abstracto: círculo + ondas + etiqueta de rol (código)."""
    import matplotlib.patches as mpatches
    roles = {"panel": [("A", "MODERADORA", ACCENT), ("B", "INVITADO", "#38bdf8")]}
    if kind == "panel":
        for i, (tag, role, col) in enumerate(roles["panel"]):
            cx = 0.10 + i * 0.13
            ax = fig.add_axes([cx - 0.035, 0.135, 0.07, 0.125])
            ax.set_xlim(0, 1)
            ax.set_ylim(0, 1)
            ax.axis("off")
            ax.add_patch(mpatches.Circle((0.5, 0.55), 0.22, color=col, alpha=0.9))
            for r in (0.32, 0.42):
                ax.add_patch(mpatches.Arc((0.5, 0.55), r * 2, r * 2, theta1=200,
                                         theta2=340, color=col, lw=2))
            ax.text(0.5, 0.10, f"{tag}", ha="center", va="center", fontsize=10,
                    color=FG, fontweight="bold")
            fig.text(cx, 0.115, role, color=MUTED, fontsize=11, ha="center")
    else:
        label = {"presentadora": "PRESENTADORA (voz sintética)",
                 "profesora": "PROFESORA (voz sintética)",
                 "financiera": "DIRECTORA FINANCIERA (voz sintética)"}.get(
                     kind, "VOZ SINTÉTICA")
        ax = fig.add_axes([0.055, 0.135, 0.07, 0.125])
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.axis("off")
        ax.add_patch(mpatches.Circle((0.5, 0.55), 0.22, color=ACCENT, alpha=0.9))
        for r in (0.32, 0.42):
            ax.add_patch(mpatches.Arc((0.5, 0.55), r * 2, r * 2, theta1=200,
                                     theta2=340, color=ACCENT, lw=2))
        fig.text(0.14, 0.175, label, color=MUTED, fontsize=12, va="center")


def render_slide_png(piece, slide, chart_paths, workdir):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.image as mpimg

    n = slide["n"]
    fig = plt.figure(figsize=(WIDTH / DPI, HEIGHT / DPI), dpi=DPI)
    fig.patch.set_facecolor(BG)
    has_chart = bool(slide["chart"])

    fig.text(0.05, 0.96, slide["title"], color=ACCENT,
             fontsize=40, fontweight="bold", va="top", ha="left")
    fig.text(0.05, 0.875, f"ATLAS LIVE ES  •  {piece['title']}  •  {piece['role']}",
             color=MUTED, fontsize=16, va="top", ha="left")
    # Badge quemado "Synthetic demo".
    fig.text(0.95, 0.955, " Synthetic demo ", color="#101820", fontsize=18,
             fontweight="bold", va="top", ha="right",
             bbox=dict(boxstyle="round,pad=0.4", facecolor=ACCENT, edgecolor="none"))

    y = 0.74
    for b in slide["bullets"]:
        wrapped = textwrap.wrap(b, width=30 if has_chart else 52)
        fig.text(0.05, y, "•  " + wrapped[0], color=FG,
                 fontsize=27, va="top", ha="left")
        y -= 0.075
        for cont in wrapped[1:]:
            fig.text(0.075, y, cont, color=FG, fontsize=27, va="top", ha="left")
            y -= 0.065
        y -= 0.02

    _draw_avatar(fig, slide.get("avatar", "presentadora"))

    fig.text(0.05, 0.045, f"Slide {n} / {len(piece['slides'])}", color=MUTED,
             fontsize=15)
    fig.text(0.95, 0.045, f"{piece['file']}  •  ES", color=MUTED,
             fontsize=15, ha="right")

    if has_chart:
        img = mpimg.imread(chart_paths[slide["chart"]])
        ax = fig.add_axes([0.55, 0.12, 0.40, 0.62])
        ax.imshow(img)
        ax.axis("off")
        for spine in ax.spines.values():
            spine.set_visible(False)

    p = os.path.join(workdir, f"slide_{n:02d}.png")
    fig.savefig(p, dpi=DPI, facecolor=BG)
    plt.close(fig)
    return p


# ----------------------------------------------------------------------------
# TTS (piper carlfm) + voz B del panel (variante ffmpeg documentada) + ffmpeg.
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


def tts(text, wav_path):
    cmd = [PIPER_BIN, "--model", VOICE_ES, "--output_file", wav_path]
    r = subprocess.run(cmd, input=text.encode("utf-8"),
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if r.returncode != 0:
        raise RuntimeError(f"piper failed: {r.stderr.decode()[-500:]}")
    return wav_path


def voice_b_variant(ff, wav_in, wav_out):
    """Voz B del panel: carlfm con pitch x0.85 vía ffmpeg (documentada como
    variante sintética, NO segunda persona; ver PROVENANCE_ES.md)."""
    run([ff, "-y", "-v", "error", "-i", wav_in, "-af",
         "asetrate=16000*0.85,aresample=16000", wav_out])
    return wav_out


def wav_info(wav_path):
    with wave.open(wav_path, "rb") as w:
        frames = w.getnframes()
        rate = w.getframerate()
        raw = w.readframes(frames)
    dur = frames / rate if rate else 0.0
    n = len(raw) // 2
    esum = 0
    for i in range(n):
        v = struct.unpack("<h", raw[2 * i:2 * i + 2])[0]
        esum += v * v
    rms = (esum / max(n, 1)) ** 0.5
    return {"frames": frames, "rate": rate, "dur": dur, "rms": rms,
            "bytes": os.path.getsize(wav_path)}


def concat_wavs(wav_paths, out_path, gap_s=0.0):
    """Concatena WAVs mono s16 (misma tasa) con silencio intermedio."""
    params = None
    chunks = []
    for p in wav_paths:
        with wave.open(p, "rb") as w:
            if params is None:
                params = (w.getnchannels(), w.getsampwidth(), w.getframerate())
            assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == params, \
                f"wav params mismatch: {p}"
            chunks.append(w.readframes(w.getnframes()))
    gap = b"\x00" * int(params[2] * gap_s) * params[0] * params[1]
    body = gap.join(chunks)
    with wave.open(out_path, "wb") as w:
        w.setnchannels(params[0])
        w.setsampwidth(params[1])
        w.setframerate(params[2])
        w.writeframes(body)
    return out_path


def build_segment(ff, png, wav, dur, seg_path):
    cmd = [ff, "-y", "-v", "error",
           "-loop", "1", "-framerate", str(FPS), "-t", f"{dur:.3f}",
           "-i", png, "-i", wav,
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
           "-af", f"apad=whole_dur={dur:.3f}",
           "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
           "-t", f"{dur:.3f}", "-movflags", "+faststart", seg_path]
    run(cmd)


def concat_segments(ff, seg_paths, final_path, workdir):
    lst = os.path.join(workdir, "concat_%s.txt" % os.path.basename(final_path))
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


def write_srt(path, entries):
    """entries = [(t0, t1, text)]."""
    with open(path, "w") as f:
        for i, (t0, t1, tx) in enumerate(entries, 1):
            f.write(f"{i}\n{srt_time(t0)} --> {srt_time(t1)}\n{tx}\n\n")


# ----------------------------------------------------------------------------
# SNR: ruido blanco gaussiano generado por código (semilla fija, CC0 por
# construcción: sin assets externos). Mezcla determinista y verificable.
# ----------------------------------------------------------------------------
def mix_snr(clean, rate, snr_db, seed):
    """Devuelve (mezcla int16, snr_medido_db). Puro numpy, testeable."""
    import numpy as np
    rng = np.random.default_rng(seed)
    x = clean.astype(np.float64)
    p_sig = float((x ** 2).mean())
    noise = rng.standard_normal(len(x))
    p_n = float((noise ** 2).mean())
    g = (p_sig / (p_n * 10 ** (snr_db / 10))) ** 0.5
    y = x + g * noise
    peak = max(1.0, abs(y).max() / 32767.0)
    y = (y / peak).clip(-32768, 32767).astype(np.int16)
    # SNR medido sobre la mezcla sin normalizar (antes del clip).
    meas = 10 * np.log10(p_sig / ((g * noise) ** 2).mean())
    return y, float(meas)


def write_wav_int16(path, samples, rate=16000):
    import numpy as np
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(np.asarray(samples, dtype=np.int16).tobytes())
    return path


def read_wav_mono16(path):
    import numpy as np
    with wave.open(path, "rb") as w:
        assert (w.getnchannels(), w.getsampwidth()) == (1, 2), path
        rate = w.getframerate()
        raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype=np.int16).copy(), rate


# ----------------------------------------------------------------------------
# Construcción por pieza.
# ----------------------------------------------------------------------------
def build_piece(pid, chart_paths, ff, skip_tts_check=False):
    piece = PIECES[pid]
    workdir = os.path.join(WORK, piece["file"])
    os.makedirs(workdir, exist_ok=True)
    is_panel = piece["kind"] == "panel"

    timings = []   # por slide: (t0, t1)
    srt_entries = []
    seg_paths = []
    slide_wavs = []
    truth_rows = []
    t = 0.0
    for slide in piece["slides"]:
        n = slide["n"]
        png = render_slide_png(piece, slide, chart_paths, workdir)
        if is_panel:
            # TTS por turno (voz A/B) + concat con pausa entre turnos.
            turn_wavs, turn_durs = [], []
            for j, seg in enumerate(slide["segs"]):
                raw = os.path.join(workdir, f"narr_{n:02d}_{j:02d}_raw.wav")
                tts(seg["text"], raw)
                if seg.get("voice", "A") == "B":
                    vb = os.path.join(workdir, f"narr_{n:02d}_{j:02d}_B.wav")
                    voice_b_variant(ff, raw, vb)
                    raw = vb
                info = wav_info(raw)
                assert info["frames"] > 0 and info["rms"] > 50, f"bad wav {raw}"
                turn_wavs.append(raw)
                turn_durs.append(info["dur"])
            wav = os.path.join(workdir, f"narr_{n:02d}.wav")
            concat_wavs(turn_wavs, wav, gap_s=0.4)
            # SRT por turno.
            tt = t
            for seg, wv, dd in zip(slide["segs"], turn_wavs, turn_durs):
                srt_entries.append((tt, tt + dd, seg["text"]))
                tt += dd + 0.4
        else:
            wav = os.path.join(workdir, f"narr_{n:02d}.wav")
            tts(" ".join(s["text"] for s in slide["segs"]), wav)
            info = wav_info(wav)
            assert info["frames"] > 0 and info["rms"] > 50, f"bad wav {wav}"
        dur = wav_info(wav)["dur"] + PAD_AFTER_AUDIO
        print(f"  slide {n:02d}: {wav_info(wav)['dur']:.1f}s audio -> seg {dur:.1f}s",
              flush=True)
        seg = os.path.join(workdir, f"seg_{n:02d}.mp4")
        build_segment(ff, png, wav, dur, seg)
        timings.append((t, t + dur))
        if not is_panel:
            srt_entries.append((t, t + dur,
                                " ".join(s["text"] for s in slide["segs"])))
        t += dur
        seg_paths.append(seg)
        slide_wavs.append(wav)
        for s in slide["segs"]:
            row = {"id": s["cid"], "claim": s["cid"], "lang": "es",
                   "piece": pid, "slide": n, "text": s["text"],
                   "t_aprox": round(timings[-1][0], 2),
                   "category": s["category"], "expected": s["expected"],
                   "ref": s["ref"], "sources": s.get("sources", []),
                   "ground_truth": s.get("ground_truth", ""),
                   "calculation": s.get("calculation")}
            if s.get("voice"):
                row["voice"] = s["voice"]
            truth_rows.append(row)

    final = os.path.join(OUTDIR, f"{piece['file']}.mp4")
    concat_segments(ff, seg_paths, final, workdir)
    write_srt(os.path.join(OUTDIR, f"{piece['file']}.srt"), srt_entries)
    print(f"  VIDEO {final}: {t:.1f}s total ({t/60:.2f} min)", flush=True)
    assert 150 <= t <= 300, f"duration {t:.1f}s outside 2.5-5 min!"

    # WAV limpio (mix 16k) + 3 SNR.
    clean = os.path.join(workdir, "mix_clean_16k.wav")
    concat_wavs(slide_wavs, clean, gap_s=PAD_AFTER_AUDIO)
    samples, rate = read_wav_mono16(clean)
    assert rate == 16000, f"unexpected rate {rate}"
    clean_out = os.path.join(OUTDIR, f"{piece['file']}_clean.wav")
    write_wav_int16(clean_out, samples)
    for snr in SNR_LEVELS:
        mixed, meas = mix_snr(samples, rate, snr, seed=NOISE_SEED + snr)
        assert abs(meas - snr) < 0.5, f"SNR off: {meas} vs {snr}"
        write_wav_int16(os.path.join(OUTDIR, f"{piece['file']}_snr{snr}db.wav"),
                        mixed)
        print(f"  SNR {snr} dB (medido {meas:.2f})", flush=True)

    os.makedirs(TRUTH_ES, exist_ok=True)
    tp = os.path.join(TRUTH_ES, f"{piece['file']}.json")
    with open(tp, "w") as f:
        json.dump(truth_rows, f, ensure_ascii=False, indent=2)
    ids = [r["id"] for r in truth_rows]
    assert len(ids) == len(set(ids)) == 40, f"{pid}: {len(ids)} rows!"
    print(f"  TRUTH {tp} ({len(ids)} rows)", flush=True)
    return {"duration_s": round(t, 1), "mp4": final, "truth": tp,
            "n_claims": len(ids)}


# ----------------------------------------------------------------------------
# Medición WER + recall por pieza (transcribe API, bias ES + keywords).
# ----------------------------------------------------------------------------
def measure_piece(pid, retries=0):
    from live.oido.asr_file import transcribe_file
    piece = PIECES[pid]
    api_key = os.environ.get("MODEL_API_KEY", "")
    if not api_key:
        raise SystemExit("MODEL_API_KEY not configured; source .env first.")
    tp = os.path.join(TRUTH_ES, f"{piece['file']}.json")
    with open(tp, encoding="utf-8") as f:
        rows = json.load(f)
    texts = [r["text"] for r in rows]
    keywords = extract_keywords(texts)
    wav = os.path.join(OUTDIR, f"{piece['file']}_clean.wav")
    print(f"  transcribe {wav} ({len(texts)} spans, {len(keywords)} kws)",
          flush=True)
    resp = transcribe_file(wav, api_key, language_bias=("Spanish",),
                           keywords=tuple(keywords))
    transcript = (resp.get("transcript") or "").strip()
    if not transcript:
        transcript = " ".join((t.get("transcript") or "")
                              for t in (resp.get("turns") or [])).strip()
    audio_ms = resp.get("audioDurationMs", 0) or 0
    usd = (audio_ms / 3600000.0) * USD_PER_HOUR
    ref = srt_plain_text(os.path.join(OUTDIR, f"{piece['file']}.srt"))
    w = wer_details(ref, transcript)
    rec = asr_recall(texts, transcript)
    print(f"  WER {w['wer']:.4f} recall {rec['recall']:.4f} "
          f"({len(rec['hits'])}/{rec['n']}) ${usd:.4f}", flush=True)
    for miss in rec["misses"]:
        print(f"    MISS: {miss[:100]}", flush=True)
    report = {"piece": pid, "wer": w["wer"], "recall": rec["recall"],
              "n_claims": rec["n"], "misses": rec["misses"],
              "n_keywords": len(keywords), "audio_ms": audio_ms,
              "usd": round(usd, 4), "retries": retries}
    rp = os.path.join(OUTDIR, f"{piece['file']}_wer.json")
    with open(rp, "w") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    with open(os.path.join(WORK, piece["file"], "transcript.txt"), "w") as f:
        f.write(transcript + "\n")
    return report


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(65536), b""):
            h.update(blk)
    return h.hexdigest()


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build + measure ES synth corpus")
    ap.add_argument("--pieces", default="K1,K2,P1,C1,C2,E1")
    ap.add_argument("--skip-build", action="store_true")
    ap.add_argument("--measure-only", action="store_true")
    ap.add_argument("--check", action="store_true",
                    help="exit 1 unless WER<0.15 and recall>=0.9 on all pieces")
    args = ap.parse_args(argv)
    pids = [p.strip() for p in args.pieces.split(",") if p.strip()]
    assert set(pids) <= set(PIECES), f"unknown pieces: {pids}"
    os.makedirs(OUTDIR, exist_ok=True)
    os.makedirs(WORK, exist_ok=True)

    build_results = {}
    if not args.measure_only:
        assert os.path.exists(PIPER_BIN), f"missing piper: {PIPER_BIN}"
        assert os.path.exists(VOICE_ES + ".json"), f"missing voice: {VOICE_ES}"
        ff = ffmpeg_bin()
        print("== charts ==", flush=True)
        chart_paths = make_charts()
        for k, v in chart_paths.items():
            print(f"  {k}: {os.path.getsize(v)} bytes", flush=True)
        for pid in pids:
            print(f"== build {pid} [{PIECES[pid]['file']}] ==", flush=True)
            build_results[pid] = build_piece(pid, chart_paths, ff)

    reports, total_usd = {}, 0.0
    do_measure = (not args.skip_build) or args.measure_only
    if do_measure:
        for pid in pids:
            print(f"== measure {pid} ==", flush=True)
            rep = measure_piece(pid)
            # Máx 2 reintentos por pieza (re-transcribe; mismo audio).
            att = 0
            while (rep["wer"] >= 0.15 or rep["recall"] < 0.9) and att < 2:
                att += 1
                print(f"  retry {att}/2 for {pid}", flush=True)
                rep = measure_piece(pid, retries=att)
            reports[pid] = rep
            total_usd += rep["usd"]

    print("== summary ==", flush=True)
    ok = True
    for pid in pids:
        b = build_results.get(pid, {})
        r = reports.get(pid, {})
        line = f"  {pid}: {b.get('duration_s', '?')}s"
        if r:
            line += f" WER={r['wer']:.4f} recall={r['recall']:.4f} ${r['usd']:.4f}"
            if r["wer"] >= 0.15 or r["recall"] < 0.9:
                ok = False
        print(line, flush=True)
    print(f"  TOTAL USD (this run): ${total_usd:.4f}", flush=True)
    if args.check and not ok:
        raise SystemExit("gates failed: need WER<0.15 and recall>=0.9")
    return 0


if __name__ == "__main__":
    sys.exit(main())
