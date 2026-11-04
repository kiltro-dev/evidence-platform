# Checklist humano — submit Atlas Live (~15 min, 3 pasos)

Todo lo pegable está en `live/demo/SUBMISSION.md`. Este checklist solo
ordena el trabajo manual que un humano debe hacer (login, video, portal).

## Paso 1 — Subir el video (~5 min)

1. Grabar el MP4 con tu voz siguiendo `live/demo/RECORDING_GUIDE.md`
   (~2:30, guion frase por frase en inglés). Alternativa: el MP4
   TTS `data/demo/atlas-live-demo.mp4` (2:37.0, en máquina del dev,
   data/ no se commitea — pedirlo si se prefiere esa versión).
2. Subirlo a la plataforma que pida el portal (YouTube no-listado o
   adjunto directo — confirmar en el portal tras login, ver Campos).
3. Copiar la URL final / ID del adjunto. Pegarla en el campo de video
   del portal Y en `SUBMISSION.md` § Links (reemplaza el TODO-HUMAN).

Números congelados 3-nov 12:00 Santiago (`live/claims_registry.json` +
`scripts/check_claims.py` en verde): pegar los de SUBMISSION.md tal
cual, sin re-redactar cifras.

## Paso 2 — Portal: pegar textos exactos (~8 min)

Abrir `live/demo/SUBMISSION.md` y pegar sección por sección. Mapeo
esperado (confirmar nombres exactos tras login — ver Campos):

| Portal (nombre típico) | Pegar desde SUBMISSION.md |
|---|---|
| Project title | § Title |
| Tagline / short | § Short description |
| Description | § Long description |
| Problem | § Problem |
| Target user | § Target user |
| Impact | § Impact |
| Meta tech usage | § Use of Meta capabilities |
| Architecture | § Architecture |
| Tech stack | § Stack |
| Limitations | § Honest limits |
| Reproduce / run | § How to reproduce |
| Licenses | § Licenses & data rights |
| Demo video | URL del Paso 1 |
| Repo + commit | repo público + hash del commit del submit |

Regla: pegar verbatim; si el portal trunca un campo, recortar por el
final de esa sección (nunca re-redactar cifras).

## Paso 3 — Billing y verificación final (~2 min)

1. Confirmar que el video se reproduce (abrir la URL en incógnito).
2. Confirmar que el repo es visible para jueces (o invitarlos).
3. Revisar el gasto live total declarado: sumar ONLY las filas
   verificadas (M1e $0.0213 · M2 $0.70 · M2d $0.96 · M3 $0.0248 ·
   M5 ≈$0.02 · M6 $0.2914 · M7 ≈$0.03 — cada una en su `live/M*.md`).
   NO declarar un total redondo inventado.
4. Marcar el submit como final SOLO si el video sigue
   `live/demo/RECORDING_GUIDE.md` (voz humana) o es el MP4 TTS
   alternativo verificado (2:37.0, `live/F2_VIDEO.md` QA).

## Campos a confirmar del portal (tras login)

Completar aquí después del primer login; hoy son desconocidos:

- [ ] Nombre exacto del campo de video + plataforma aceptada: ___
- [ ] Duración máxima del video (¿2:55 pedido por bases?): ___
- [ ] Formato repo: ¿URL pública / invitación / ZIP?: ___
- [ ] ¿Pide commit hash? ¿Rama?: ___
- [ ] Límite de caracteres en short/long: ___
- [ ] ¿Campo separado para Meta tech usage?: ___
- [ ] Deadline exacto + zona horaria: ___
- [ ] ¿Pide lista de equipo / roles?: ___
