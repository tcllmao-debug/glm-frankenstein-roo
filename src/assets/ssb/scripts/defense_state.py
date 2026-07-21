import threading, time
from typing import Any

class DefenseState:
    _instance = None
    _lock = threading.Lock()
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._active = False
                    cls._instance._activated_at_ms = 0.0
                    cls._instance._activation_history = []
        return cls._instance
    @property
    def active(self): return self._active
    @property
    def activated_at_ms(self): return self._activated_at_ms
    def activate(self, reason="activated via button"):
        was = self._active
        self._active = True
        self._activated_at_ms = time.time() * 1000.0
        self._activation_history.append({"action":"activate","ts":self._activated_at_ms,"reason":reason})
        return {"status":"active","was_already_active":was,"reason":reason,"activated_at_ms":self._activated_at_ms}
    def deactivate(self, reason="deactivated via button"):
        was = self._active
        dur = 0.0
        if was and self._activated_at_ms > 0: dur = (time.time()*1000.0) - self._activated_at_ms
        self._active = False
        self._activated_at_ms = 0.0
        self._activation_history.append({"action":"deactivate","ts":time.time()*1000.0,"reason":reason,"duration_active_ms":dur})
        return {"status":"inactive","was_active":was,"reason":reason,"duration_active_ms":dur}
    def toggle(self):
        return self.deactivate() if self._active else self.activate()
    def get_state(self):
        return {"active":self._active,"activated_at_ms":self._activated_at_ms,
                "activation_count":len(self._activation_history),
                "recent_history":self._activation_history[-10:]}

DEFENSE = DefenseState()
