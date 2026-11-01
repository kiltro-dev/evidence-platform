# live/mic — micrófono real en vivo (F2-5)

Fuente de micrófono REAL hacia `transcribe_stream`: un solo proceso ffmpeg
lee el dispositivo de captura (PulseAudio o ALSA, autodetectado) y emite PCM
s16le mono 24 kHz. Cero dependencias nuevas de audio: solo ffmpeg (binario
estático de `imageio_ffmpeg`, con fallback al ffmpeg del sistema si aporta
el demuxer `pulse` que al estático le falta).

Etiqueta de fuente: `"live-microphone"`. Nunca `"simulated-stream"`.

## Contrato

- Chunks PCM mono s16 24 kHz (`BYTES_PER_SEC = 48000`, importado de
  `live.ingest.demux`, idéntico al que espera `live.oido`).
- `t_media = bytes_leídos / 48000`; el primer chunk empieza en `0.0`.
- `MicCapture.chunks()` es un async generator de bytes: entra directo en
  `transcribe_stream` / `PCMFeed`. `iter_audio()` rinde `(t_media, bytes)`.
- Autodetección = solo dispositivos REALES (pulse/alsa, con prueba de
  apertura+lectura en vivo). `lavfi` y replay de fichero existen solo como
  doubles explícitos de test (`for_test_lavfi` / `for_test_file`), con
  etiqueta `test-double:*`: un entorno sin micrófono jamás recibe audio
  sintético haciéndose pasar por micrófono.

## Degradación honesta (CI/headless)

Sin micrófono, `MicCapture().start()` y `detect_microphone()` lanzan
`NoMicrophoneError` con el log de intentos (binarios, demuxers,
dispositivos) y cómo habilitarlo. El test de micrófono vivo salta
(`pytest.skip`); el resto de tests usan doubles offline.

Comprobado en este host: sin micrófono utilizable (ffmpeg estático sin
demuxer `pulse`; ALSA sin nodos de captura en `/dev/snd`, solo `timer`;
hay socket PulseServer de WSLg pero ningún binario puede hablarle).
Ver `live/F2_MIC_CLIP.md`.

## Wiring propuesto en `live/run_live.py` (NO implementado aquí)

Añadir flag `--mic` que sustituya el `FifoStream` (simstream) por
`MicCapture`. Snippet orientativo:

```python
# --- en build_parser() ---
parser.add_argument("--mic", action="store_true",
                    help="capture live microphone instead of --video replay "
                    "(label live-microphone; needs PulseAudio/ALSA)")

# --- en amain(), donde hoy se crea FifoStream ---
from live.mic.capture import MicCapture, NoMicrophoneError

if args.mic:
    try:
        stream = MicCapture(duration=args.duration).start()
    except NoMicrophoneError as e:
        raise SystemExit(f"--mic: {e}")
    assert stream.source_label == "live-microphone"
    audio_source = stream.chunks()   # async bytes -> transcribe_stream
else:
    stream = FifoStream(args.video, ...).start()
    audio_source = stream.chunks()

# ... transcribe_stream(cfg, audio_source, on_event=...) igual que hoy ...
# ... finally: stream.close() (ambas clases lo soportan) ...
```

Notas:

- `t0_wall` existe en ambas clases con la misma semántica (primer byte ≈
  media 0), así que el modelo de latencia no cambia.
- Con micrófono, `--video` y el detector de streams dejan de aplicar; el
  resto del pipeline (ASR → detector → bus → WS) no se toca.
- El `duration` limita la captura (`-t` en salida), igual que hoy limita
  el replay: útil para clips de coste acotado.

## Probar en local (con micrófono)

```bash
.venv/bin/python - <<'EOF'
import asyncio, sys
sys.path.insert(0, '.')
from live.mic.capture import MicCapture, detect_microphone

print(detect_microphone())  # kind pulse/alsa + binario + device

async def main():
    cap = MicCapture(duration=3.0).start()
    n = 0
    async for t_media, pcm in cap.iter_audio():
        n += 1
    cap.close()
    print(f"chunks={n} media_s={cap.media_seconds:.2f} t0={cap.t0_wall}")

asyncio.run(main())
EOF
```

Sin micrófono, el mismo script falla con `NoMicrophoneError` y el motivo
exacto. Los tests offline (`live/tests/test_f2_mic.py`) cubren el
contrato con doubles lavfi/wav sin hardware.
