#!/usr/bin/env python3
"""
Config Manager for OpenIPC Bridge
Централизованное управление конфигурацией и синхронизация с интеграцией HA
"""

import os
import re
import json
import yaml
import logging
import shutil
import socket
from datetime import datetime
from typing import Dict, List, Optional, Any
from pathlib import Path

from security import valid_camera_host, safe_name

logger = logging.getLogger(__name__)

# Placeholder returned instead of secrets; sending it back keeps the stored value
MASK = "********"
CAMERA_TYPES = {"openipc", "beward", "vivotek"}
TTS_FORMATS = {"pcm", "alaw"}
RECORDING_QUALITIES = {"low", "medium", "high"}
# Поля камеры, которые можно добавить через API, даже если их ещё нет в конфиге
UPDATABLE_CAMERA_KEYS = {
    "name", "type", "username", "password", "port", "rtsp_port",
    "snapshot_endpoints", "tts_endpoint", "tts_format", "relay_endpoints",
    "recording", "detection", "snapshots", "osd",
}

# ==================== КОНСТАНТЫ ====================

CONFIG_FILE = "/config/openipc_bridge_config.yaml"
CONFIG_BACKUP_DIR = "/config/openipc_backups"
ADDON_OPTIONS_FILE = "/data/options.json"
INTEGRATION_CONFIG_FILE = "/config/.storage/openipc_config.json"

# Структура конфигурации по умолчанию
DEFAULT_CONFIG = {
    "version": "1.3.2",
    "system": {
        "max_recordings": 5
    },
    "telegram": {
        "bot_token": "",
        "chat_id": "",
        "video_quality": "high",
        "max_size_mb": 50
    },
    "cameras": [],
    "tts": {
        "provider": "google",
        "google": {
            "language": "ru",
            "slow": False
        },
        "rhvoice": {
            "voice": "anna",
            "language": "ru",
            "speed": 1.0
        },
        "yandex": {
            "api_key": "",
            "language": "ru",
            "emotion": "neutral",
            "speed": 1.0
        }
    },
    "logging": {
        "level": "INFO",
        "debug_qr": True,
        "max_debug_images": 100
    },
    "last_sync": None
}


# ==================== КЛАСС МЕНЕДЖЕРА КОНФИГУРАЦИИ ====================

class ConfigManager:
    """Центральный менеджер конфигурации с синхронизацией"""
    
    def __init__(self):
        self.config = DEFAULT_CONFIG.copy()
        self._ensure_backup_dir()
        self.load_config()
    
    def _ensure_backup_dir(self):
        """Создать директорию для бэкапов"""
        try:
            os.makedirs(CONFIG_BACKUP_DIR, exist_ok=True)
            logger.debug(f"✅ Backup directory ready: {CONFIG_BACKUP_DIR}")
        except Exception as e:
            logger.error(f"❌ Failed to create backup dir: {e}")
    
    def load_config(self):
        """Загрузить конфигурацию из файла"""
        try:
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    file_config = yaml.safe_load(f)
                    if file_config:
                        self._deep_merge(self.config, file_config)
                        logger.info(f"✅ Configuration loaded from {CONFIG_FILE}")
                        logger.info(f"   Cameras: {len(self.config['cameras'])}")
                        
                        # Выводим список камер для отладки
                        for i, cam in enumerate(self.config['cameras']):
                            logger.debug(f"   Camera {i+1}: {cam.get('name')} ({cam.get('ip')}) - {cam.get('type')}")
            else:
                self.save_config(create_backup=False)
                logger.info(f"✅ Created default configuration at {CONFIG_FILE}")
        except Exception as e:
            logger.error(f"❌ Failed to load config: {e}")
            logger.exception(e)
        self._apply_addon_options()

    def _apply_addon_options(self):
        """Map Home Assistant add-on options onto the runtime config."""
        try:
            if not os.path.exists(ADDON_OPTIONS_FILE):
                return
            with open(ADDON_OPTIONS_FILE, 'r', encoding='utf-8') as f:
                options = json.load(f)
            telegram = self.config.setdefault("telegram", {})
            if options.get("telegram_bot_token"):
                telegram["bot_token"] = options["telegram_bot_token"]
            if options.get("telegram_chat_id"):
                telegram["chat_id"] = str(options["telegram_chat_id"])
        except Exception as e:
            logger.error(f"❌ Failed to read add-on options: {e}")
    
    def _deep_merge(self, base: dict, update: dict):
        """Рекурсивное слияние словарей"""
        for key, value in update.items():
            if isinstance(value, dict) and key in base and isinstance(base[key], dict):
                self._deep_merge(base[key], value)
            else:
                base[key] = value
    
    def save_config(self, create_backup: bool = True) -> bool:
        """Сохранить конфигурацию в файл"""
        try:
            # Обновляем временную метку
            self.config['last_sync'] = datetime.now().isoformat()
            
            # Создаем бэкап если нужно
            if create_backup and os.path.exists(CONFIG_FILE):
                self._create_backup()
            
            # Сохраняем конфигурацию
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                yaml.dump(self.config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
            os.chmod(CONFIG_FILE, 0o600)  # contains camera passwords
            
            logger.info("✅ Configuration saved successfully")
            
            # Синхронизируем с интеграцией
            self.sync_with_integration()
            
            return True
        except Exception as e:
            logger.error(f"❌ Failed to save config: {e}")
            return False
    
    def _create_backup(self) -> Optional[str]:
        """Создать резервную копию конфигурации"""
        try:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            backup_file = os.path.join(CONFIG_BACKUP_DIR, f"config_{timestamp}.yaml")
            shutil.copy2(CONFIG_FILE, backup_file)
            logger.info(f"✅ Backup created: {backup_file}")
            return backup_file
        except Exception as e:
            logger.error(f"❌ Failed to create backup: {e}")
            return None
    
    def list_backups(self) -> List[Dict]:
        """Получить список всех бэкапов"""
        backups = []
        try:
            if not os.path.exists(CONFIG_BACKUP_DIR):
                return backups
                
            for file in os.listdir(CONFIG_BACKUP_DIR):
                if file.startswith('config_') and file.endswith('.yaml'):
                    filepath = os.path.join(CONFIG_BACKUP_DIR, file)
                    stat = os.stat(filepath)
                    backups.append({
                        'name': file,
                        'size': stat.st_size,
                        'created': datetime.fromtimestamp(stat.st_ctime).isoformat(),
                        'modified': datetime.fromtimestamp(stat.st_mtime).isoformat()
                    })
            
            # Сортируем по дате (новые сверху)
            backups.sort(key=lambda x: x['created'], reverse=True)
            return backups
        except Exception as e:
            logger.error(f"Error listing backups: {e}")
            return []
    
    def restore_backup(self, backup_name: str) -> bool:
        """Восстановить конфигурацию из бэкапа"""
        try:
            if (not safe_name(backup_name) or not backup_name.startswith('config_')
                    or not backup_name.endswith('.yaml')):
                logger.error(f"Invalid backup name: {backup_name!r}")
                return False
            backup_path = os.path.join(CONFIG_BACKUP_DIR, backup_name)
            if not os.path.exists(backup_path):
                logger.error(f"Backup not found: {backup_name}")
                return False
            
            # Создаем бэкап текущей конфигурации
            self._create_backup()
            
            # Восстанавливаем
            shutil.copy2(backup_path, CONFIG_FILE)
            
            # Перезагружаем конфигурацию
            self.load_config()
            
            logger.info(f"✅ Restored from backup: {backup_name}")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to restore backup: {e}")
            return False
    
    def get_camera(self, camera_ip: str) -> Optional[Dict]:
        """Получить конфигурацию камеры по IP"""
        try:
            logger.debug(f"🔍 Looking for camera: '{camera_ip}'")
            
            # Точное совпадение
            for cam in self.config['cameras']:
                if cam.get('ip') == camera_ip:
                    logger.debug(f"✅ Found camera: {cam.get('name')}")
                    return cam
            
            # Пробуем без пробелов
            camera_ip_clean = camera_ip.strip()
            for cam in self.config['cameras']:
                if cam.get('ip', '').strip() == camera_ip_clean:
                    logger.debug(f"✅ Found camera (after cleanup): {cam.get('name')}")
                    return cam
            
            logger.warning(f"❌ Camera {camera_ip} not found")
            logger.debug(f"   Available IPs: {[c.get('ip') for c in self.config['cameras']]}")
            return None
        except Exception as e:
            logger.error(f"Error getting camera {camera_ip}: {e}")
            return None
    
    def get_camera_by_name(self, camera_name: str) -> Optional[Dict]:
        """Получить конфигурацию камеры по имени"""
        try:
            for cam in self.config['cameras']:
                if cam.get('name') == camera_name:
                    return cam
            return None
        except Exception as e:
            logger.error(f"Error getting camera by name {camera_name}: {e}")
            return None
    
    def add_camera(self, camera_data: Dict) -> bool:
        """Добавить новую камеру"""
        try:
            # Проверяем, нет ли уже такой камеры
            existing = self.get_camera(camera_data.get('ip'))
            if existing:
                logger.warning(f"Camera {camera_data.get('ip')} already exists")
                return False
            
            # Добавляем обязательные поля если их нет
            if 'port' not in camera_data:
                camera_data['port'] = 80
            if 'rtsp_port' not in camera_data:
                camera_data['rtsp_port'] = 554
            if 'recording' not in camera_data:
                camera_data['recording'] = {
                    'enabled': True,
                    'mode': 'continuous',
                    'segment_duration': 300,
                    'archive_depth': 7,
                    'quality': 'medium',
                    'fps': 15,
                    'format': 'mp4'
                }
            
            if 'detection' not in camera_data:
                camera_data['detection'] = {
                    'motion': True,
                    'qr': True,
                    'sensitivity': 5,
                    'person': False,
                    'car': False,
                    'lpr': False,
                    'person_confidence': 70,
                    'car_confidence': 80,
                    'whitelist': []
                }
            
            if 'snapshots' not in camera_data:
                camera_data['snapshots'] = {
                    'enabled': True,
                    'format': 'jpg'
                }
            
            if 'osd' not in camera_data:
                camera_data['osd'] = {
                    'enabled': camera_data.get('type') != 'beward',
                    'port': 9000,
                    'time_format': "%d.%m.%Y %H:%M:%S",
                    'regions': {}
                }
            
            self.config['cameras'].append(camera_data)
            logger.info(f"✅ Camera added: {camera_data.get('name')} ({camera_data.get('ip')})")
            
            return True
        except Exception as e:
            logger.error(f"Error adding camera: {e}")
            return False
    
    def update_camera(self, camera_ip: str, updates: Dict) -> bool:
        """Обновить данные камеры"""
        try:
            for i, cam in enumerate(self.config['cameras']):
                if cam.get('ip') == camera_ip:
                    # Рекурсивно обновляем поля
                    self._deep_merge(cam, updates)
                    logger.info(f"✅ Camera updated: {cam.get('name')} ({camera_ip})")
                    return True
            
            logger.warning(f"Camera {camera_ip} not found for update")
            return False
        except Exception as e:
            logger.error(f"Error updating camera {camera_ip}: {e}")
            return False
    
    def delete_camera(self, camera_ip: str) -> bool:
        """Удалить камеру"""
        try:
            initial_count = len(self.config['cameras'])
            self.config['cameras'] = [c for c in self.config['cameras'] if c.get('ip') != camera_ip]
            
            if len(self.config['cameras']) < initial_count:
                logger.info(f"✅ Camera deleted: {camera_ip}")
                return True
            else:
                logger.warning(f"Camera {camera_ip} not found for deletion")
                return False
        except Exception as e:
            logger.error(f"Error deleting camera {camera_ip}: {e}")
            return False
    
    def get_cameras_list(self, include_status: bool = False) -> List[Dict]:
        """Получить список всех камер"""
        cameras = []
        for cam in self.config['cameras']:
            cam_copy = cam.copy()
            if include_status:
                cam_copy['online'] = self._check_camera_online(cam.get('ip'))
            cameras.append(cam_copy)
        return cameras
    
    def _check_camera_online(self, ip: str, port: int = 80, timeout: int = 1) -> bool:
        """Проверить, доступна ли камера по сети"""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            result = sock.connect_ex((ip, port))
            sock.close()
            return result == 0
        except Exception as e:
            logger.debug(f"Error checking camera {ip}: {e}")
            return False
    
    def update_recording_settings(self, camera_ip: str, settings: Dict) -> bool:
        """Обновить настройки записи для камеры"""
        try:
            cam = self.get_camera(camera_ip)
            if not cam:
                logger.error(f"Camera {camera_ip} not found")
                return False
            
            if not isinstance(settings, dict):
                logger.error("Recording settings must be an object")
                return False
            settings = self._sanitize_recording_settings(settings)
            
            if 'recording' not in cam:
                cam['recording'] = {}
            
            cam['recording'].update({
                'mode': settings.get('mode', 'continuous'),
                'enabled': settings.get('enabled', True),
                'segment_duration': settings.get('segment_duration', 300),
                'archive_depth': settings.get('archive_depth', 7),
                'quality': settings.get('quality', 'medium'),
                'fps': settings.get('fps', 15),
                'format': settings.get('format', 'mp4')
            })
            
            if 'detection' not in cam:
                cam['detection'] = {}
            
            cam['detection'].update({
                'motion': settings.get('detect_motion', True),
                'qr': settings.get('detect_qr', True),
                'sensitivity': settings.get('sensitivity', 5)
            })
            
            # Обновляем расширенные настройки детекции если есть
            if 'person' in settings:
                cam['detection']['person'] = settings['person']
            if 'car' in settings:
                cam['detection']['car'] = settings['car']
            if 'lpr' in settings:
                cam['detection']['lpr'] = settings['lpr']
            if 'person_confidence' in settings:
                cam['detection']['person_confidence'] = settings['person_confidence']
            if 'car_confidence' in settings:
                cam['detection']['car_confidence'] = settings['car_confidence']
            if 'whitelist' in settings:
                cam['detection']['whitelist'] = settings['whitelist']
            
            if 'schedule' in settings:
                cam['recording']['schedule'] = settings['schedule']
            
            logger.info(f"✅ Recording settings updated for {camera_ip}")
            return True
        except Exception as e:
            logger.error(f"Error updating recording settings for {camera_ip}: {e}")
            return False
    
    # ==================== ВАЛИДАЦИЯ И МАСКИРОВАНИЕ СЕКРЕТОВ ====================

    @staticmethod
    def _int_in_range(value, default, low, high):
        try:
            return max(low, min(high, int(value)))
        except (TypeError, ValueError):
            return default

    def _sanitize_recording_settings(self, settings: Dict) -> Dict:
        """Ограничить значения, которые попадают в имена файлов и аргументы ffmpeg"""
        clean = dict(settings)
        if 'mode' in clean and clean['mode'] not in ('continuous', 'motion', 'disabled'):
            clean['mode'] = 'continuous'
        if 'quality' in clean and clean['quality'] not in RECORDING_QUALITIES:
            clean['quality'] = 'medium'
        if 'format' in clean and clean['format'] not in ('mp4', 'mkv'):
            clean['format'] = 'mp4'
        for key, default, low, high in (
            ('segment_duration', 300, 10, 3600),
            ('archive_depth', 7, 1, 365),
            ('fps', 15, 1, 60),
            ('sensitivity', 5, 1, 10),
            ('person_confidence', 70, 0, 100),
            ('car_confidence', 80, 0, 100),
        ):
            if key in clean:
                clean[key] = self._int_in_range(clean[key], default, low, high)
        return clean

    @staticmethod
    def _valid_endpoint(value) -> bool:
        return (
            value == ""
            or (isinstance(value, str) and value.startswith("/") and not value.startswith("//")
                and len(value) <= 512 and not re.search(r"[\s\\\x00-\x1f]", value))
        )

    def validate_camera(self, cam: Dict, trusted_hosts=()):
        """Проверить и нормализовать настройки камеры. Возвращает (camera, error)."""
        if not isinstance(cam, dict):
            return None, "Camera must be an object"
        raw_host = cam.get('ip')
        if isinstance(raw_host, str) and raw_host.strip() in trusted_hosts:
            host = raw_host.strip()
        else:
            host = valid_camera_host(raw_host)
        if not host:
            return None, f"Invalid camera address: {raw_host!r} (must be a LAN IP or hostname)"

        name = cam.get('name') or host
        if (not isinstance(name, str) or not name.strip() or len(name) > 100
                or re.search(r'[<>"`\x00-\x1f]', name)):
            return None, "Invalid camera name"

        cam_type = cam.get('type', 'openipc')
        if cam_type not in CAMERA_TYPES:
            return None, f"Invalid camera type: {cam_type!r}"

        for key in ('username', 'password'):
            value = cam.get(key, '')
            if value is None:
                value = ''
            if not isinstance(value, str) or len(value) > 128 or re.search(r'[\x00-\x1f]', value):
                return None, f"Invalid {key}"

        clean = dict(cam)
        clean.update(ip=host, name=name.strip(), type=cam_type,
                     username=cam.get('username') or '', password=cam.get('password') or '')

        for key, default in (('port', 80), ('rtsp_port', 554)):
            try:
                port = int(cam.get(key, default))
            except (TypeError, ValueError):
                return None, f"Invalid {key}"
            if not 1 <= port <= 65535:
                return None, f"Invalid {key}"
            clean[key] = port

        endpoints = cam.get('snapshot_endpoints')
        if endpoints is not None:
            if (not isinstance(endpoints, list)
                    or not all(isinstance(e, str) and self._valid_endpoint(e) for e in endpoints)):
                return None, "Invalid snapshot_endpoints"
        if not self._valid_endpoint(cam.get('tts_endpoint', '')):
            return None, "Invalid tts_endpoint"
        relays = cam.get('relay_endpoints')
        if relays is not None:
            if (not isinstance(relays, dict)
                    or not all(self._valid_endpoint(v) for v in relays.values())):
                return None, "Invalid relay_endpoints"
        if cam.get('tts_format', 'pcm') not in TTS_FORMATS:
            return None, "Invalid tts_format"

        if isinstance(clean.get('recording'), dict):
            clean['recording'] = self._sanitize_recording_settings(clean['recording'])

        # osd.port подставляется в URL камеры, поэтому только число
        if 'osd' in clean:
            if not isinstance(clean['osd'], dict):
                return None, "Invalid osd settings"
            if 'port' in clean['osd']:
                try:
                    osd_port = int(clean['osd']['port'])
                except (TypeError, ValueError):
                    return None, "Invalid osd port"
                if not 1 <= osd_port <= 65535:
                    return None, "Invalid osd port"
                clean['osd'] = dict(clean['osd'], port=osd_port)
        return clean, None

    def replace_cameras(self, new_cameras):
        """Заменить список камер (с валидацией). Пароль-маска сохраняет прежний пароль."""
        if not isinstance(new_cameras, list) or len(new_cameras) > 64:
            return False, "cameras must be a list"
        old_by_ip = {c.get('ip'): c for c in self.config['cameras']}
        old_by_name = {c.get('name'): c for c in self.config['cameras']}
        trusted = set(old_by_ip)
        cleaned, seen = [], set()
        for cam in new_cameras:
            if isinstance(cam, dict) and cam.get('password') == MASK:
                old = old_by_ip.get(cam.get('ip')) or old_by_name.get(cam.get('name'))
                cam = dict(cam, password=(old or {}).get('password', ''))
            clean, error = self.validate_camera(cam, trusted)
            if error:
                return False, error
            if clean['ip'] in seen:
                return False, f"Duplicate camera address: {clean['ip']}"
            seen.add(clean['ip'])
            cleaned.append(clean)
        self.config['cameras'] = cleaned
        if not self.save_config():
            return False, "Failed to save config"
        return True, None

    def update_camera_validated(self, camera_ip: str, updates: Dict):
        """Обновить поля существующей камеры с валидацией. Возвращает (found, error)."""
        import copy
        cam = self.get_camera(camera_ip)
        if not cam:
            return False, "not_found"
        if not isinstance(updates, dict):
            return True, "Invalid data"
        merged = copy.deepcopy(cam)
        for key, value in updates.items():
            if (key in merged or key in UPDATABLE_CAMERA_KEYS) and not (key == 'password' and value == MASK):
                merged[key] = value
        clean, error = self.validate_camera(merged, {c.get('ip') for c in self.config['cameras']})
        if error:
            return True, error
        cam.clear()
        cam.update(clean)
        if not self.save_config():
            return True, "Failed to save config"
        return True, None

    def mask_camera(self, cam: Dict) -> Dict:
        masked = dict(cam)
        if masked.get('password'):
            masked['password'] = MASK
        return masked

    def public_config(self) -> Dict:
        """Копия конфигурации без секретов (для отдачи в браузер)"""
        import copy
        cfg = copy.deepcopy(self.config)
        cfg['cameras'] = [self.mask_camera(c) for c in cfg.get('cameras', [])]
        telegram = cfg.get('telegram', {})
        if telegram.get('bot_token'):
            telegram['bot_token'] = MASK
        yandex = cfg.get('tts', {}).get('yandex', {})
        if yandex.get('api_key'):
            yandex['api_key'] = MASK
        return cfg

    def sync_with_integration(self):
        """Синхронизировать конфигурацию с интеграцией HA"""
        try:
            # Создаем директорию .storage если её нет
            os.makedirs(os.path.dirname(INTEGRATION_CONFIG_FILE), exist_ok=True)
            
            # Сохраняем упрощенную версию для интеграции
            integration_config = {
                'cameras': [],
                'last_sync': self.config['last_sync'],
                'version': self.config['version']
            }
            
            for cam in self.config['cameras']:
                integration_config['cameras'].append({
                    'ip': cam.get('ip'),
                    'name': cam.get('name'),
                    'type': cam.get('type'),
                    'port': cam.get('port', 80),
                    'rtsp_port': cam.get('rtsp_port', 554),
                    'recording_enabled': cam.get('recording', {}).get('enabled', True),
                    'recording_mode': cam.get('recording', {}).get('mode', 'continuous'),
                    'motion_detection': cam.get('detection', {}).get('motion', True),
                    'qr_detection': cam.get('detection', {}).get('qr', True)
                })
            
            # Сохраняем для интеграции
            with open(INTEGRATION_CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(integration_config, f, indent=2, ensure_ascii=False)
            
            logger.info("✅ Configuration synced with integration")
            
            # Здесь можно добавить вызов API HA для обновления
            self._notify_integration()
            
        except Exception as e:
            logger.error(f"❌ Failed to sync with integration: {e}")
    
    def _notify_integration(self):
        """Уведомить интеграцию об изменении конфигурации"""
        try:
            import requests
            # Пытаемся импортировать токен из server, но не критично если нет
            try:
                from server import SUPERVISOR_TOKEN, HASS_URL
            except ImportError:
                SUPERVISOR_TOKEN = os.environ.get('SUPERVISOR_TOKEN', '')
                HASS_URL = os.environ.get('HASS_URL', 'http://supervisor/core')
            
            if not SUPERVISOR_TOKEN:
                logger.debug("No supervisor token, skipping integration notification")
                return
            
            headers = {
                "Authorization": f"Bearer {SUPERVISOR_TOKEN}",
                "Content-Type": "application/json",
            }
            
            # Отправляем событие в HA
            url = f"{HASS_URL}/api/events/openipc_config_updated"
            response = requests.post(
                url, 
                headers=headers, 
                json={"timestamp": datetime.now().isoformat()}, 
                timeout=2
            )
            
            if response.status_code == 200:
                logger.debug("Integration notified about config update")
            else:
                logger.debug(f"Failed to notify integration: HTTP {response.status_code}")
                
        except Exception as e:
            logger.debug(f"Failed to notify integration: {e}")
    
    def import_from_ha(self, ha_cameras: List[Dict]) -> Dict:
        """Импортировать камеры из HA"""
        imported = []
        updated = []
        skipped = []
        
        logger.info(f"📥 Importing {len(ha_cameras)} cameras from HA")
        
        for ha_cam in ha_cameras:
            # Проверяем, что ha_cam - словарь, а не строка
            if isinstance(ha_cam, str):
                logger.warning(f"⚠️ Skipping string entry: {ha_cam}")
                skipped.append(ha_cam)
                continue
                
            camera_ip = ha_cam.get('ip')
            if not camera_ip:
                logger.warning(f"⚠️ Camera has no IP, skipping")
                skipped.append(str(ha_cam))
                continue
            if not valid_camera_host(camera_ip):
                logger.warning(f"⚠️ Skipping camera with invalid host: {camera_ip!r}")
                skipped.append(str(camera_ip))
                continue
            
            existing = self.get_camera(camera_ip)
            
            if existing:
                # Обновляем существующую
                old_name = existing.get('name')
                existing['name'] = ha_cam.get('name', existing['name'])
                existing['type'] = ha_cam.get('device_type', existing.get('type', 'openipc'))
                existing['username'] = ha_cam.get('username', existing.get('username', ''))
                existing['password'] = ha_cam.get('password', existing.get('password', ''))
                existing['port'] = ha_cam.get('port', existing.get('port', 80))
                existing['rtsp_port'] = ha_cam.get('rtsp_port', existing.get('rtsp_port', 554))
                
                updated.append({
                    'ip': camera_ip,
                    'old_name': old_name,
                    'new_name': existing['name']
                })
                logger.info(f"✅ Updated camera: {existing['name']} ({camera_ip})")
            else:
                # Создаем новую
                device_type = ha_cam.get('device_type', 'openipc')
                
                new_camera = {
                    'name': ha_cam.get('name', f"Camera {camera_ip}"),
                    'ip': camera_ip,
                    'type': device_type,
                    'username': ha_cam.get('username', ''),
                    'password': ha_cam.get('password', ''),
                    'port': ha_cam.get('port', 80),
                    'rtsp_port': ha_cam.get('rtsp_port', 554),
                    'recording': {
                        'enabled': True,
                        'mode': 'continuous',
                        'segment_duration': 300,
                        'archive_depth': 7,
                        'quality': 'medium',
                        'fps': 15,
                        'format': 'mp4'
                    },
                    'detection': {
                        'motion': True,
                        'qr': True,
                        'sensitivity': 5,
                        'person': False,
                        'car': False,
                        'lpr': False,
                        'person_confidence': 70,
                        'car_confidence': 80,
                        'whitelist': []
                    },
                    'snapshots': {
                        'enabled': True,
                        'format': 'jpg'
                    },
                    'osd': {
                        'enabled': device_type != 'beward',
                        'port': 9000,
                        'time_format': "%d.%m.%Y %H:%M:%S",
                        'regions': {}
                    }
                }
                
                # Добавляем специфичные для Beward настройки
                if device_type == 'beward':
                    new_camera['tts_format'] = 'alaw'
                    new_camera['tts_endpoint'] = '/cgi-bin/audio/transmit.cgi'
                    new_camera['snapshot_endpoints'] = ['/cgi-bin/jpg/image.cgi', '/cgi-bin/snapshot.cgi']
                    new_camera['relay_endpoints'] = {
                        "relay1_on": "/cgi-bin/alarmout_cgi?action=set&Output=0&Status=1",
                        "relay1_off": "/cgi-bin/alarmout_cgi?action=set&Output=0&Status=0",
                        "relay2_on": "/cgi-bin/alarmout_cgi?action=set&Output=1&Status=1",
                        "relay2_off": "/cgi-bin/alarmout_cgi?action=set&Output=1&Status=0"
                    }
                elif device_type == 'vivotek':
                    new_camera['snapshot_endpoints'] = ['/cgi-bin/viewer/video.jpg', '/cgi-bin/video.jpg']
                else:  # openipc
                    new_camera['snapshot_endpoints'] = ['/image.jpg', '/cgi-bin/api.cgi?cmd=Snap&channel=0']
                    new_camera['tts_endpoint'] = '/play_audio'
                    new_camera['tts_format'] = 'pcm'
                
                self.config['cameras'].append(new_camera)
                imported.append({
                    'ip': camera_ip,
                    'name': new_camera['name']
                })
                logger.info(f"✅ Imported new camera: {new_camera['name']} ({camera_ip})")
        
        logger.info(f"📊 Import summary: {len(imported)} new, {len(updated)} updated, {len(skipped)} skipped")
        return {
            'imported': imported,
            'updated': updated,
            'skipped': skipped,
            'total': len(self.config['cameras'])
        }
    
    def export_for_ha(self) -> Dict:
        """Экспортировать конфигурацию для HA"""
        cameras = []
        for cam in self.config['cameras']:
            cameras.append({
                'ip': cam.get('ip'),
                'name': cam.get('name'),
                'type': cam.get('type'),
                'port': cam.get('port', 80),
                'rtsp_port': cam.get('rtsp_port', 554),
                'recording_enabled': cam.get('recording', {}).get('enabled', True),
                'recording_mode': cam.get('recording', {}).get('mode', 'continuous'),
                'motion_detection': cam.get('detection', {}).get('motion', True),
                'qr_detection': cam.get('detection', {}).get('qr', True)
            })
        
        return {
            'version': self.config.get('version'),
            'cameras': cameras,
            'last_sync': self.config.get('last_sync')
        }
    
    def get_max_recordings(self) -> int:
        """Получить максимальное количество одновременных записей"""
        return self.config.get('system', {}).get('max_recordings', 5)
    
    def set_max_recordings(self, value: int) -> bool:
        """Установить максимальное количество одновременных записей"""
        try:
            if 'system' not in self.config:
                self.config['system'] = {}
            self.config['system']['max_recordings'] = max(1, min(20, value))
            return True
        except Exception as e:
            logger.error(f"Error setting max recordings: {e}")
            return False


# ==================== ГЛОБАЛЬНЫЙ ЭКЗЕМПЛЯР ====================

_config_manager = None

def get_config_manager() -> ConfigManager:
    """Получить глобальный экземпляр менеджера конфигурации"""
    global _config_manager
    if _config_manager is None:
        _config_manager = ConfigManager()
    return _config_manager