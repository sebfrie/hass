
# OpenIPC Camera for Home Assistant 🚀

Integration for managing OpenIPC, Beward, and Vivotek cameras in Home Assistant, with a powerful web interface (OpenIPC Bridge addon) for advanced features.

---

## ✨ Features

### 📹 Video Surveillance
- RTSP streams and snapshots
- Recording to HA media folder with OSD overlay
- PTZ control for Vivotek
- Relay control for Beward

### 📊 Monitoring
- CPU temperature, FPS, bitrate
- SD card status, network statistics
- License plate recognition (LNPR) for Beward
- Smart `majestic` process monitoring with auto-restart via SSH after 3 failed health checks

### 🔊 Text-to-Speech (TTS)
- **Google TTS** — cloud-based, 30+ languages, high quality
- **RHVoice** — local, offline, "Anna" voice (requires separate addon)
- Support for Beward (A-law) and OpenIPC (PCM)

### 📱 Notifications
- Send photos and videos to Telegram
- Daily Telegram reports on camera health at 9:00 PM

### 🖥️ OpenIPC Bridge Addon Web UI
- Camera management interface with import from HA integration
- Visual OSD editor (drag-and-drop regions, real-time preview, templates, BMP logo support)
- QR code scanner & generator with scan history, CSV export, and Telegram send
- TTS provider selection (Google / RHVoice)
- Recording archive with filters, built-in video player, and timeline with event marks
- Snapshot management with modal viewer, download, and Telegram send

---

## 🆕 What's New (March 2026)

### 🎥 New Recording System
- Configure recording quality (`high`/`medium`/`low`), FPS (5–30), and segment duration (1 min – 1 hr) per camera
- Limit on simultaneous recordings (default 5) to prevent system overload, configurable in the UI
- Automatic cleanup of old recordings based on configured archive depth (days)

### 🔔 Notifications
- Centralized Telegram settings (token, chat ID, video quality) on a dedicated Notifications page
- Send snapshots directly from the archive player, with optional video compression

### ⚙️ UI Improvements
- New System tab in camera settings to manage simultaneous recording limits and view statistics
- Absolute timestamps on the archive video timeline

---

## 🚧 Future Plans

- **Advanced Object Detection:** AI-based detection of people, vehicles, and other objects
- **License Plate Recognition (LPR):** Current LPR sensors are placeholders; full support planned

---

## 📦 Installation

### 1. OpenIPC Bridge Addon (required for advanced features)

1. Go to **Settings → Add-ons → Add-on Store**
2. Click ⋮ (top right) → **Repositories**
3. Add: `https://github.com/sebfrie/hass`
4. Find and install **OpenIPC Bridge**
5. The addon web UI will be available at `http://[YOUR-HA-IP]:5000`

### 2. OpenIPC Camera Integration

**Via HACS (recommended):**
1. Open HACS → Integrations → ⋮ → Custom repositories
2. Add `https://github.com/sebfrie/hass` with category **Integration**
3. Find **OpenIPC Camera** and install
4. Restart Home Assistant

**Manual:**
Copy the `custom_components/openipc` folder to `/config/custom_components/` and restart HA.

---

## 🎮 Using the Addon Web Interface

Open `http://[YOUR-HA-IP]:5000` after installation.

### 📹 Cameras Tab
- **Import from HA** — click "Import cameras from HA" to pull all cameras from the OpenIPC integration automatically
- **Manual add** — fill in name, IP address, type (OpenIPC / Beward / Vivotek), and credentials

### 🖥️ OSD Tab (On-Screen Display)
1. Select a camera
2. Configure up to 4 regions (Region 0 = logo/BMP, Regions 1–3 = text)
3. Drag regions to position; appearance options: color, font size (8–72 px), font, opacity (0–255)
4. Supported variables: `$t` (time), `$B` (bitrate), `$C` (frame counter), `$M` (memory)
5. Save and load templates

### 📸 QR Scanner & Generator Tab
- **Scanner:** select camera → optional expected code → Start scanning → fires `openipc_qr_detected` event in HA
- **Generator:** enter text/URL → adjust size, colors, error correction → Generate → save or send to Telegram
- **History:** all scans saved with CSV export and clipboard copy

### 🔊 TTS Tab
1. Select camera → choose provider (Google TTS / RHVoice) → select language → enter text → Test
2. Debug audio files saved to `/config/www/tts_debug_*.pcm`

---

## 🤖 Blueprints

### Blueprint 1: QR Scanner

```
https://github.com/sebfrie/hass/blob/main/blueprints/automation/openipc/qr_scanner.yaml
```

Starts scanning on button press, validates the code, and can trigger TTS notification, relay control, and Telegram notification.

### Blueprint 2: Door Opening Video Recording

```
https://github.com/sebfrie/hass/blob/main/blueprints/automation/openipc/door_recording.yaml
```

Configurable settings: door sensor, camera, media player, TTS provider, recording duration (10–600 s), OSD regions, Telegram toggle, post-recording clock duration.

What it does:
1. Clears old OSD
2. TTS: "Door open, starting recording"
3. Sets OSD with date/time (ticking clock)
4. Records video for the configured duration
5. TTS: "Recording complete"
6. Sends video to Telegram (if enabled)
7. Clears OSD

---

## 📝 Automation Examples

**Simple TTS on motion:**
```yaml
alias: "Say Hello on Motion"
trigger:
  - platform: state
    entity_id: binary_sensor.openipc_sip_motion
    to: "on"
action:
  - service: media_player.play_media
    target:
      entity_id: media_player.openipc_sip_speaker
    data:
      media_content_id: "Hello, you are on camera!"
      media_content_type: "tts"
      extra:
        provider: "rhvoice"  # or "google"
```

**QR scan for gate control:**
```yaml
alias: "Open Gate with QR Code"
trigger:
  - platform: event
    event_type: openipc_qr_detected
condition:
  - condition: template
    value_template: "{{ trigger.event.data.data == 'secret_gate_code' }}"
action:
  - service: switch.turn_on
    entity_id: switch.gate_relay
  - delay:
      seconds: 1
  - service: switch.turn_off
    entity_id: switch.gate_relay
  - service: media_player.play_media
    target:
      entity_id: media_player.openipc_sip_speaker
    data:
      media_content_id: "Access granted, gate open"
      media_content_type: "tts"
```

---

## 🔧 Setting Up RHVoice (Local TTS)

1. Add repository: `https://github.com/definitio/ha-rhvoice-addon`
2. Install the **RHVoice** Home Assistant addon
3. Install the RHVoice integration via HACS
4. In integration settings, set host to `localhost`

---

## 📊 Project Structure

```
/
├── custom_components/openipc/     # HA Integration (HACS)
├── openipc-bridge/                # HA Addon
│   ├── Dockerfile
│   ├── config.yaml
│   ├── build.yaml
│   ├── run.sh
│   ├── server.py
│   ├── stream_monitor.py
│   ├── recording_api.py
│   ├── config_manager.py
│   ├── camera_monitor.py
│   ├── daily_reporter.py
│   ├── tts_generate_openipc.sh
│   ├── tts_generate.sh
│   ├── tts_generate_rhvoice.sh
│   └── templates/
└── blueprints/automation/openipc/
    ├── qr_scanner.yaml
    └── door_recording.yaml
```

---

## 🆘 Troubleshooting

**Logs:**
- Integration: Settings → System → Logs → `openipc`
- Addon: Supervisor → OpenIPC Bridge → Logs
- TTS debug files: `/config/www/tts_debug_*.pcm`

**OSD not appearing:**
- Check OSD service is running on camera: `ps | grep osd`
- Verify port 9000 is accessible: `netstat -tlnp | grep 9000`
- Check opacity setting is not 0

**TTS not working:**
- Verify camera is reachable (ping)
- Check the correct endpoint is configured (`/play_audio` for OpenIPC)
- For RHVoice: ensure the separate addon is running

**Camera import from HA not working:**
- Verify `http` is listed in `manifest.json` dependencies
- Check the API endpoint: `http://[HA_IP]:8123/api/openipc/cameras`

---

## 🤝 Contributing

- ⭐ Star us on GitHub
- 🐛 Report bugs in Issues
- 📝 Improve documentation
- 🔧 Submit Pull Requests

---

## 📜 License

MIT License — OpenIPC Community 🚀
