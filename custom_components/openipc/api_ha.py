"""API endpoints for OpenIPC integration to communicate with addon."""
from homeassistant.core import HomeAssistant
from homeassistant.components.http import HomeAssistantView
import logging

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

DATA_API_REGISTERED = f"{DOMAIN}_cameras_api_registered"

class OpenIPCCamerasView(HomeAssistantView):
    """View to return list of OpenIPC cameras (incl. credentials) for addon."""
    
    url = "/api/openipc/cameras"
    name = "api:openipc:cameras"
    # Authentication is enforced by Home Assistant itself: the token must be a
    # valid HA access token. The addon reaches this via the Supervisor proxy,
    # which authenticates as the Supervisor system user.
    requires_auth = True
    
    async def get(self, request):
        """Handle GET request."""
        hass = request.app["hass"]
        
        # The response contains camera credentials. Only system-generated admin
        # users get them: in practice the Supervisor, through which the bridge
        # add-on calls. Accounts of people and their long-lived tokens are
        # refused, so a leaked token or a script in a dashboard cannot read them.
        user = request.get("hass_user")
        if user is None or not (user.is_admin and user.system_generated):
            _LOGGER.warning(
                "Rejected access to the cameras API by %s",
                user.name if user else "an unknown user",
            )
            return self.json(
                {"success": False, "error": "Forbidden"}, status_code=403
            )
        
        # Получаем список камер из интеграции
        cameras = await async_get_cameras(hass)
        
        return self.json({
            "success": True,
            "cameras": cameras
        })

async def async_get_cameras(hass: HomeAssistant) -> list:
    """Получить список всех камер OpenIPC."""
    cameras = []
    
    for entry_id, coordinator in hass.data[DOMAIN].items():
        if entry_id == "config":
            continue
        
        if hasattr(coordinator, 'host'):
            # Получаем модель из данных
            model = "Unknown"
            firmware = "Unknown"
            if coordinator.data and 'parsed' in coordinator.data:
                model = coordinator.data['parsed'].get('model', 'Unknown')
                firmware = coordinator.data['parsed'].get('firmware', 'Unknown')
            
            camera_data = {
                "entry_id": entry_id,
                "name": coordinator.entry.data.get('name', 'OpenIPC Camera'),
                "ip": coordinator.host,
                "port": coordinator.port,
                "username": coordinator.username,
                "password": coordinator.password,
                "device_type": coordinator.entry.data.get('device_type', 'openipc'),
                "rtsp_port": coordinator.rtsp_port,
                "available": coordinator.data.get('available', False) if coordinator.data else False,
                "model": model,
                "firmware": firmware,
            }
            
            # Добавляем информацию о Beward если есть
            if hasattr(coordinator, 'beward') and coordinator.beward:
                camera_data['beward'] = {
                    'relay_count': getattr(coordinator.beward, 'relay_count', 1),
                    'model': getattr(coordinator.beward, '_model', 'DS07P-LP')
                }
            
            # Добавляем информацию о Vivotek если есть
            if hasattr(coordinator, 'vivotek') and coordinator.vivotek:
                camera_data['vivotek'] = {
                    'ptz_available': getattr(coordinator.vivotek, 'ptz_available', False),
                    'model': getattr(coordinator.vivotek, 'model_name', 'SD9364-EHL')
                }
            
            cameras.append(camera_data)
    
    return cameras

async def async_register_api(hass: HomeAssistant) -> None:
    """Register the cameras API once; only the OpenIPC Bridge add-on uses it."""
    if hass.data.get(DATA_API_REGISTERED):
        return
    hass.data[DATA_API_REGISTERED] = True
    hass.http.register_view(OpenIPCCamerasView)
    _LOGGER.info("✅ OpenIPC API endpoints registered at /api/openipc/cameras")
