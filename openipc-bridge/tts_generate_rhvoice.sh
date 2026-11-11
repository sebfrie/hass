#!/bin/bash
# Script for generating speech via a separate RHVoice add-on (ha-rhvoice-addon)

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

TEMP_WAV="$(mktemp /tmp/tts_XXXXXX.wav)" || exit 1
trap 'rm -f "$TEMP_WAV"' EXIT

# Choose the voice depending on the language
if [ "$LANG_CODE" = "ru" ]; then
    VOICE="anna"
elif [ "$LANG_CODE" = "en" ]; then
    VOICE="slt"
else
    VOICE="anna"
fi

# JSON is built via json.dumps (the text is passed as an argument), not by string concatenation
PAYLOAD="$(python3 -c 'import json, sys; print(json.dumps({"text": sys.argv[1], "voice": sys.argv[2]}))' "$MESSAGE" "$VOICE")" || exit 1

echo "🔊 Requesting RHVoice from separate addon: voice=$VOICE"

# Try different addresses for reaching the RHVoice add-on
# The definitio/ha-rhvoice-addon add-on is usually available on port 8080
RHVOICE_URLS=(
    "http://localhost:8080/say"
    "http://172.30.32.1:8080/say"
    "http://supervisor:8080/say"
    "http://rhvoice:8080/say"
)

SUCCESS=false
RESPONSE_MSG=""

for URL in "${RHVOICE_URLS[@]}"; do
    echo "Trying RHVoice at $URL"
    
    # Send a POST request with the text (format depends on the add-on)
    # For definitio/ha-rhvoice-addon we use JSON
    RESPONSE_MSG=$(curl -X POST "$URL" \
        -H "Content-Type: application/json" \
        --data-binary "$PAYLOAD" \
        --output "$TEMP_WAV" \
        --write-out "%{http_code}" \
        --silent)
        
    if [ "$RESPONSE_MSG" = "200" ] && [ -f "$TEMP_WAV" ] && [ -s "$TEMP_WAV" ]; then
        echo "✅ RHVoice response received from $URL (HTTP 200)"
        SUCCESS=true
        break
    else
        echo "❌ Failed with HTTP $RESPONSE_MSG"
    fi
done

if [ "$SUCCESS" = false ]; then
    echo "❌ All RHVoice endpoints failed, falling back to Google TTS"
    /app/tts_generate_openipc.sh "$MESSAGE" "$LANG_CODE" "$FILENAME"
    exit $?
fi

echo "✅ RHVoice WAV created: $TEMP_WAV"

# Convert to PCM 8000Hz, 16bit, mono for OpenIPC
ffmpeg -y -i "$TEMP_WAV" -ar 8000 -ac 1 -f s16le "$FILENAME" 2>/dev/null

if [ ! -f "$FILENAME" ]; then
    echo "❌ PCM conversion failed, falling back to Google TTS"
    /app/tts_generate_openipc.sh "$MESSAGE" "$LANG_CODE" "$FILENAME"
    exit $?
fi

SIZE=$(wc -c < "$FILENAME")
echo "✅ RHVoice PCM file created: $FILENAME ($SIZE bytes)"
exit 0