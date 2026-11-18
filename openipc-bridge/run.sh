#!/usr/bin/with-contenv bashio
set -e

echo "========================================="
echo "Starting OpenIPC Bridge Addon (HLS version)"
echo "========================================="
echo "$(date): Starting up..."

# Создаем директорию для HLS сегментов
mkdir -p /tmp/hls
chmod 755 /tmp/hls
mkdir -p /media/openipc/recordings /media/openipc/snapshots /media/openipc/exports /media/openipc/logos

# Remote access to the API/Web UI requires the access_password option (localhost is trusted)
if [ -z "$(bashio::config 'access_password')" ]; then
    echo "$(date): ⚠️ access_password is empty - remote requests to port 5000 are rejected until it is set"
fi

# Безопасно добавляем системные пути
SYS_SITE_PACKAGES=$(python3 -c 'import sys; print(f"/usr/lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages")' 2>/dev/null || true)
if [ -n "$SYS_SITE_PACKAGES" ] && [ -d "$SYS_SITE_PACKAGES" ]; then
    export PYTHONPATH="${SYS_SITE_PACKAGES}${PYTHONPATH:+:$PYTHONPATH}"
    echo "$(date): PYTHONPATH set to: $PYTHONPATH"
fi

# Информация о системе
echo "$(date): Python version: $(python3 --version)"
echo "$(date): Python path: $(which python3)"

# Проверяем наличие токена Supervisor
if [ -n "$SUPERVISOR_TOKEN" ]; then
    echo "$(date): ✅ SUPERVISOR_TOKEN available"
else
    echo "$(date): ⚠️ SUPERVISOR_TOKEN not set"
fi

# Проверяем наши скрипты
if [ -f /app/tts_generate_openipc.sh ]; then
    echo "$(date): ✅ OpenIPC TTS script available"
else
    echo "$(date): ❌ OpenIPC TTS script missing"
    exit 1
fi

if [ -f /app/tts_generate.sh ]; then
    echo "$(date): ✅ Beward TTS script available"
else
    echo "$(date): ❌ Beward TTS script missing"
    exit 1
fi

# Запускаем Flask сервер
if [ -f /app/server.py ]; then
    echo "$(date): Starting Flask server on port 5000..."
    cd /app
    python3 server.py &
    FLASK_PID=$!
    echo "$(date): Flask server started with PID: $FLASK_PID"
else
    echo "$(date): ❌ server.py not found!"
    exit 1
fi

echo "$(date): Addon started successfully"
echo "========================================="

# Держим контейнер запущенным
while true; do
    if ! kill -0 "$FLASK_PID" 2>/dev/null; then
        echo "$(date): ❌ Flask server (PID: $FLASK_PID) exited unexpectedly!"
        exit 1
    fi
    # Проверяем, что ffmpeg процессы не зависли
    echo "$(date): OpenIPC Bridge running... (Flask PID: ${FLASK_PID:-unknown})"
    sleep 60
done