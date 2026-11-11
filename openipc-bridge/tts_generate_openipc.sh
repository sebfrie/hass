#!/bin/bash
# Script for generating PCM for OpenIPC cameras
# Used inside the add-on

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

# Generate TTS via gTTS. The text and language are passed as arguments (argv)
# rather than substituted into the Python source.
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

# Convert to PCM 8000Hz, 16bit, mono
ffmpeg -y -i "$TEMP_MP3" -ar 8000 -ac 1 -f s16le "$FILENAME" 2>/dev/null || exit 1

echo "✅ PCM file created: $FILENAME"
exit 0