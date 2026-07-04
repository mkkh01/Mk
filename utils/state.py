"""
CTM Bot — System State
Tracks readiness, cycle count, and active status.
"""
from datetime import datetime

_state = {
    'ready': False,
    'cycles': 0,
    'last_cycle': None,
    'last_cycle_duration': 0,
    'active_coins': 0,
    'errors': 0
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
        'errors': _state['errors']
    }
