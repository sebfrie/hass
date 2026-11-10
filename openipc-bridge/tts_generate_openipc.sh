#!/bin/bash
# Скрипт для генерации PCM для OpenIPC камер
# Используется внутри аддона

MESSAGE="$1"
LANG_CODE="${2:-ru}"
FILENAME="$3"

if [ -z "$MESSAGE" ] || [ -z "$FILENAME" ]; then
    echo "Usage: $0 <text> <language> <output_file.pcm>"
    exit 1
fi

if ! [[ "$LANG_CODE" =~ ^[A-Za-z]{2,3}([-_][A-Za-z0-9]{2,4})?$ ]]; then
    echo "Invalid language code"
    exit 1
fi

TEMP_MP3="$(mktemp /tmp/tts_XXXXXX.mp3)" || exit 1
trap 'rm -f "$TEMP_MP3"' EXIT

# Генерируем TTS через gTTS. Текст и язык передаются аргументами (argv),
# а не подставляются в исходный код Python.
python3 - "$MESSAGE" "$LANG_CODE" "$TEMP_MP3" <<'PY' || exit 1
import sys
try:
    from gtts import gTTS
    text, lang, out = sys.argv[1:4]
    gTTS(text=text, lang=lang, slow=False).save(out)
    print('SUCCESS')
except Exception as e:
    print(f'ERROR: {e}')
    sys.exit(1)
PY

# Конвертируем в PCM 8000Hz, 16bit, mono
ffmpeg -y -i "$TEMP_MP3" -ar 8000 -ac 1 -f s16le "$FILENAME" 2>/dev/null || exit 1

echo "✅ PCM файл создан: $FILENAME"
exit 0