"""Deterministic pet rules. Integer units avoid rounding drift between requests."""

UNIT = 3600
MAX_STAT = 100 * UNIT
INITIAL = 80 * UNIT
NEGLECT_SECONDS = 72 * 3600
RETURN_SECONDS = 24 * 3600
MAX_ELAPSED = 24 * 3600
REACTION_SECONDS = 3
REACTION_TTL_SECONDS = 60
SLEEP_SECONDS = 3600


class GameError(Exception):
    def __init__(self, message, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


def project(row, timestamp):
    result = dict(row)
    timestamps = [row['updated_at'], row['last_care_at'], row['reaction_at']]
    timestamps += [row[key] for key in ('feed_at', 'play_at', 'sleep_at') if row[key] is not None]
    result['clock_state'] = 'UNKNOWN' if timestamp < max(timestamps) else 'OK'
    disappeared = row['last_care_at'] + NEGLECT_SECONDS
    result['disappeared_at'] = disappeared
    result['return_available_at'] = disappeared + RETURN_SECONDS
    sleep_end = (row['sleep_at'] if row['sleep_at'] is not None else row['updated_at']) + SLEEP_SECONDS if row['sleeping'] else None
    result['sleep_ends_at'] = sleep_end
    if result['clock_state'] == 'UNKNOWN':
        result['lifecycle'] = 'UNKNOWN'
    elif timestamp >= result['return_available_at']:
        result['lifecycle'] = 'RETURN_AVAILABLE'
    elif timestamp >= disappeared:
        result['lifecycle'] = 'ABSENT'
    else:
        result['lifecycle'] = 'ACTIVE'
    if result['clock_state'] == 'OK':
        elapsed = min(timestamp - row['updated_at'], MAX_ELAPSED)
        sleep_elapsed = min(elapsed, max(0, sleep_end-row['updated_at'])) if sleep_end is not None else 0
        awake_elapsed = elapsed-sleep_elapsed
        finished = sleep_end is not None and timestamp >= sleep_end
        result['satiety'] = max(0, row['satiety'] - sleep_elapsed*2 - awake_elapsed*4)
        result['energy'] = max(0, (MAX_STAT if finished else row['energy']) - awake_elapsed*3)
        result['mood'] = max(0, row['mood'] - awake_elapsed*2)
        if finished:
            result['sleeping'] = 0
            result['sleep_ends_at'] = None
    return result


def apply(row, action, timestamp):
    state = project(row, timestamp)
    if state['clock_state'] != 'OK':
        raise GameError('Server clock precedes saved pet state')
    if action == 'return':
        if state['lifecycle'] != 'RETURN_AVAILABLE':
            raise GameError('Pet return is not available yet')
        state.update(satiety=INITIAL, energy=INITIAL, mood=INITIAL, sleeping=0,
                     last_care_at=timestamp, round=row['round'] + 1, feed_at=None, play_at=None, sleep_at=None, sleep_ends_at=None)
    else:
        if state['lifecycle'] != 'ACTIVE':
            raise GameError('Pet is absent; wait until return is available')
        field, cooldown = (action + '_at', 60) if action in ('feed', 'play') else ('sleep_at', 10)
        if row[field] is not None and timestamp - row[field] < cooldown:
            raise GameError('Pet action cooldown', cooldown - (timestamp - row[field]))
        if action in ('feed', 'play') and state['sleeping']:
            raise GameError('Wake the pet before this action')
        if action == 'feed':
            if state['satiety'] == MAX_STAT:
                raise GameError('Pet is already full')
            state['satiety'] = min(MAX_STAT, state['satiety'] + 20 * UNIT)
        elif action == 'play':
            if state['energy'] < 10 * UNIT or state['mood'] == MAX_STAT:
                raise GameError('Pet cannot benefit from play right now')
            state.update(mood=min(MAX_STAT, state['mood'] + 15 * UNIT),
                         energy=state['energy'] - 10 * UNIT, satiety=max(0, state['satiety'] - 5 * UNIT))
        elif action == 'sleep':
            if state['sleeping'] or state['energy'] == MAX_STAT:
                raise GameError('Pet does not need to start sleeping')
            state['sleeping'] = 1
            state['sleep_ends_at'] = timestamp+SLEEP_SECONDS
        elif action == 'wake':
            if not state['sleeping']:
                raise GameError('Pet is already awake')
            state['sleeping'] = 0
            state['sleep_ends_at'] = None
        else:
            raise GameError('Unsupported pet action')
        state[field] = timestamp
        if action != 'wake':
            state['last_care_at'] = timestamp
    state['updated_at'] = timestamp
    return state


def emotion(state):
    if state['lifecycle'] == 'UNKNOWN':
        return 'unknown'
    if state['lifecycle'] != 'ACTIVE':
        return 'missing'
    if state['sleeping']:
        return 'sleepy'
    for key, label in [('satiety', 'hungry'), ('energy', 'tired'), ('mood', 'sad')]:
        if state[key] <= 20 * UNIT:
            return label
    return 'content'
