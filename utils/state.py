"""CTM Bot — System State — centralized pause/resume + cycle tracking."""
from datetime import datetime

_state = {
    'ready': False,
    'cycles': 0,
    'last_cycle': None,
    'last_cycle_duration': 0,
    'active_coins': 0,
    'errors': 0,
    'system_active': True,          # centralized pause/resume flag
    'circuit_breaker': False,       # triggered by max daily loss or consecutive losses
    'circuit_breaker_reason': '',
}


def mark_ready():
    _state['ready'] = True


def is_ready() -> bool:
    return _state['ready']


def tick_cycle(duration: float):
    _state['cycles'] += 1
    _state['last_cycle'] = datetime.now()
    _state['last_cycle_duration'] = round(duration, 1)


def set_coin_count(n: int):
    _state['active_coins'] = n


def inc_error():
    _state['errors'] += 1


def get_state() -> dict:
    now = datetime.now()
    ago = None
    if _state['last_cycle']:
        ago = int((now - _state['last_cycle']).total_seconds())
    return {
        'ready': _state['ready'],
        'cycles': _state['cycles'],
        'last_cycle_ago': ago,
        'duration': _state['last_cycle_duration'],
        'coins': _state['active_coins'],
        'errors': _state['errors'],
        'system_active': _state['system_active'],
        'circuit_breaker': _state['circuit_breaker'],
        'circuit_breaker_reason': _state['circuit_breaker_reason'],
    }


# ── Pause / Resume ──

def pause_system():
    _state['system_active'] = False


def resume_system():
    _state['system_active'] = True


def is_system_active() -> bool:
    return _state['system_active'] and not _state['circuit_breaker']


# ── Circuit Breaker ──

def trigger_circuit_breaker(reason: str):
    _state['circuit_breaker'] = True
    _state['circuit_breaker_reason'] = reason


def reset_circuit_breaker():
    _state['circuit_breaker'] = False
    _state['circuit_breaker_reason'] = ''
