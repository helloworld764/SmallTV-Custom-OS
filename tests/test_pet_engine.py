import pytest

from backend.app import pet_engine as game


def initial():
    return dict(satiety=game.INITIAL, energy=game.INITIAL, mood=game.INITIAL,
                sleeping=0, updated_at=1000, last_care_at=1000, round=1,
                feed_at=None, play_at=None, sleep_at=None, reaction_at=1000)


@pytest.mark.parametrize('sleeping,expected', [(0, (76, 77, 78)), (1, (78, 100, 80))])
def test_hourly_balance_and_clamps(sleeping, expected):
    row = initial() | {'sleeping': sleeping}
    state = game.project(row, 4600)
    assert tuple(state[key] / game.UNIT for key in ('satiety', 'energy', 'mood')) == expected
    assert row == initial() | {'sleeping': sleeping}


def test_subsecond_point_changes_do_not_disappear_with_frequent_observation():
    row = initial()
    first = game.project(row, 1001) | {'updated_at': 1001}
    second = game.project(first, 1002)
    direct = game.project(row, 1002)
    assert [second[k] for k in ('satiety', 'energy', 'mood')] == [direct[k] for k in ('satiety', 'energy', 'mood')]
    assert row == initial()


@pytest.mark.parametrize('elapsed,state', [(72*3600-1, 'ACTIVE'), (72*3600, 'ABSENT'),
                                         (96*3600-1, 'ABSENT'), (96*3600, 'RETURN_AVAILABLE')])
def test_neglect_and_return_exact_boundaries(elapsed, state):
    result = game.project(initial(), 1000 + elapsed)
    assert result['lifecycle'] == state
    assert result['disappeared_at'] == 1000 + 72*3600
    assert result['return_available_at'] == 1000 + 96*3600
    assert 0 <= result['energy'] <= game.MAX_STAT


def test_stat_cap_does_not_cap_neglect_and_return_starts_new_round():
    row = initial()
    capped = game.project(row, 1000 + 48*3600)
    assert (capped['satiety'], capped['energy'], capped['mood']) == (0, 8*game.UNIT, 32*game.UNIT)
    for action in ('feed', 'play', 'sleep', 'wake'):
        with pytest.raises(game.GameError):
            game.apply(row, action, 1000 + 72*3600)
    with pytest.raises(game.GameError):
        game.apply(row, 'return', 1000 + 96*3600-1)
    restored = game.apply(row, 'return', 1000 + 96*3600)
    assert restored['round'] == 2 and restored['last_care_at'] == 1000 + 96*3600
    assert [restored[key] for key in ('satiety', 'energy', 'mood')] == [game.INITIAL]*3
    assert game.project(restored, restored['updated_at'])['lifecycle'] == 'ACTIVE'


def test_backward_clock_blocks_actions_without_rebasing_saved_time():
    row = initial()
    assert game.project(row, 900)['clock_state'] == 'UNKNOWN'
    assert game.emotion(game.project(row, 900)) == 'unknown'
    for action in ('feed', 'play', 'sleep', 'wake', 'return'):
        with pytest.raises(game.GameError):
            game.apply(row, action, 900)
    assert row == initial()


def test_care_effects_cooldowns_and_waking_is_not_care():
    row = initial()
    fed = game.apply(row, 'feed', 1000)
    assert fed['satiety'] == game.MAX_STAT
    with pytest.raises(game.GameError) as error:
        game.apply(fed, 'feed', 1059)
    assert error.value.retry_after == 1
    assert game.apply(fed, 'feed', 1060)['last_care_at'] == 1060
    played = game.apply(row, 'play', 1000)
    assert [played[key] / game.UNIT for key in ('satiety', 'energy', 'mood')] == [75, 70, 95]
    sleeping = game.apply(row, 'sleep', 1000)
    with pytest.raises(game.GameError):
        game.apply(sleeping, 'wake', 1009)
    awake = game.apply(sleeping, 'wake', 1010)
    assert awake['sleeping'] == 0 and awake['last_care_at'] == 1000
    assert awake['energy'] == row['energy']


def test_full_hour_sleep_has_no_partial_reward_then_wakes_and_decays():
    row=game.apply(initial(),'sleep',1000)
    assert row['sleep_ends_at']==4600
    partial=game.project(row,4599)
    assert partial['sleeping']==1 and partial['energy']==game.INITIAL
    full=game.project(row,4600)
    assert full['sleeping']==0 and full['energy']==game.MAX_STAT and full['sleep_ends_at'] is None
    later=game.project(row,8200)
    assert (later['energy'],later['satiety'],later['mood'])==(97*game.UNIT,74*game.UNIT,78*game.UNIT)
    assert full['last_care_at']==1000


@pytest.mark.parametrize('patch,action', [({'satiety': game.MAX_STAT}, 'feed'),
    ({'energy': 0}, 'play'), ({'mood': game.MAX_STAT}, 'play'),
    ({'energy': game.MAX_STAT}, 'sleep'), ({'sleeping': 1}, 'sleep'),
    ({'sleeping': 1}, 'feed'), ({'sleeping': 1}, 'play'), ({}, 'wake')])
def test_unavailable_actions_do_not_change_care(patch, action):
    row = initial() | patch
    with pytest.raises(game.GameError):
        game.apply(row, action, 1000)
    assert row['last_care_at'] == 1000
