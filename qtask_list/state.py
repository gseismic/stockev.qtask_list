"""Redis 原子状态转换脚本。

所有脚本都遵守同一约束：先验证消息所有权，再执行状态写入；若消息没有移动，
历史、identity 和指标均不得改变。脚本只面向 standalone/Sentinel Redis 6+。
"""

from __future__ import annotations


ENQUEUE_V2_LUA = r"""
local function key_type(key)
    local value = redis.call('TYPE', key)
    if type(value) == 'table' then
        return value['ok']
    end
    return value
end

local function redis_now()
    local value = redis.call('TIME')
    return tonumber(value[1]) + tonumber(value[2]) / 1000000
end

local function owner_info(key)
    local kind = key_type(key)
    if kind == 'hash' then
        return redis.call('HGET', key, 'task_id') or '',
               redis.call('HGET', key, 'outcome') or 'none',
               tonumber(redis.call('HGET', key, 'generation') or '0')
    elseif kind == 'string' then
        return redis.call('GET', key) or '', 'none', 0
    end
    return '', '', 0
end

local function supersede_is_newer(candidate, current)
    if not current or current == '' then
        return true
    end
    local candidate_kind = string.sub(candidate, 1, 2)
    local current_kind = string.sub(current, 1, 2)
    if candidate_kind == 'i:' and current_kind == 'i:' then
        return tonumber(string.sub(candidate, 3)) > tonumber(string.sub(current, 3))
    end
    if candidate_kind == current_kind then
        return string.sub(candidate, 3) > string.sub(current, 3)
    end
    return candidate > current
end

local now = redis_now()
local has_identity = ARGV[1] == '1'
local duplicate_action = ARGV[2]
local task_id = ARGV[3]
local logical_key = ARGV[4]
local dedup_until = ARGV[5]
local raw_message = ARGV[6]
local available_at = tonumber(ARGV[7])
local record = cjson.decode(ARGV[8])
local queue_name = ARGV[9]
local has_supersede = ARGV[10] == '1'
local supersede_value = ARGV[11]
local created_at = tonumber(ARGV[12])

if redis.call('EXISTS', KEYS[5]) == 1 then
    return {'task_id_collision', task_id}
end

local duplicate_of = ''
local duplicate_outcome = ''
local generation = 1
if has_identity then
    local owner_id, owner_outcome, owner_generation = owner_info(KEYS[3])
    if owner_id == '' then
        owner_id, owner_outcome, owner_generation = owner_info(KEYS[4])
    end
    duplicate_of = owner_id
    duplicate_outcome = owner_outcome
    generation = math.max(owner_generation + 1, 1)

    if duplicate_of ~= '' and duplicate_action ~= 'allow_new' then
        local reason = 'duplicate_active'
        if duplicate_outcome ~= '' and duplicate_outcome ~= 'none' then
            reason = 'duplicate_retained'
        end
        redis.call('HINCRBY', KEYS[9], 'enqueue.' .. reason, 1)
        return {reason, duplicate_of, tostring(owner_generation)}
    end

    -- 旧版 raw logical key 使用 String；ALLOW_NEW 时由新版哈希 owner 接管。
    if key_type(KEYS[3]) ~= 'none' then
        redis.call('DEL', KEYS[3])
    end
    if KEYS[4] ~= KEYS[3] and key_type(KEYS[4]) ~= 'none' then
        redis.call('DEL', KEYS[4])
    end
    redis.call(
        'HSET', KEYS[3],
        'task_id', task_id,
        'logical_key', logical_key,
        'generation', tostring(generation),
        'outcome', 'none',
        'dedup_until', dedup_until
    )
    redis.call('PERSIST', KEYS[3])
end

for field, value in pairs(record) do
    redis.call('HSET', KEYS[5], field, tostring(value))
end
redis.call('HSET', KEYS[5], 'generation', tostring(generation))
if duplicate_of ~= '' then
    redis.call('HSET', KEYS[5], 'duplicate_override_of', duplicate_of)
end
redis.call('ZADD', KEYS[6], created_at, task_id)
redis.call('ZADD', KEYS[7], now, queue_name)

if has_supersede then
    local current = redis.call('GET', KEYS[8])
    if supersede_is_newer(supersede_value, current) then
        redis.call('SET', KEYS[8], supersede_value)
    end
end

local location = 'ready'
if available_at > now then
    redis.call('ZADD', KEYS[2], available_at, raw_message)
    location = 'delay'
else
    redis.call('LPUSH', KEYS[1], raw_message)
end
redis.call('HINCRBY', KEYS[9], 'enqueue.accepted', 1)
if duplicate_of ~= '' then
    redis.call('HINCRBY', KEYS[9], 'enqueue.override', 1)
end
return {'enqueued', task_id, duplicate_of, tostring(generation), location, tostring(now)}
"""


BEGIN_ATTEMPT_LUA = r"""
local function key_type(key)
    local value = redis.call('TYPE', key)
    if type(value) == 'table' then
        return value['ok']
    end
    return value
end

local function redis_now()
    local value = redis.call('TIME')
    return tonumber(value[1]) + tonumber(value[2]) / 1000000
end

local function release_lease()
    if ARGV[6] == '1' and redis.call('GET', KEYS[6]) == ARGV[7] then
        redis.call('DEL', KEYS[6])
    end
end

local function finalize_owner(outcome, now)
    if ARGV[3] ~= '1' or key_type(KEYS[4]) ~= 'hash' then
        return
    end
    if redis.call('HGET', KEYS[4], 'task_id') ~= ARGV[2] then
        return
    end
    redis.call('HSET', KEYS[4], 'outcome', outcome)
    local retain_until = tonumber(redis.call('HGET', KEYS[4], 'dedup_until') or '')
    if retain_until and retain_until > now then
        redis.call('EXPIREAT', KEYS[4], math.ceil(retain_until))
    else
        redis.call('DEL', KEYS[4])
    end
end

local function terminalize(outcome, code, reason, now, operational)
    redis.call(
        'HSET', KEYS[2],
        'outcome', outcome,
        'status', outcome,
        'reason_code', code,
        'reason', reason,
        'finished_at', tostring(now),
        'updated_at', tostring(now),
        'operational_message', operational
    )
    finalize_owner(outcome, now)
    if operational == '0' then
        redis.call('EXPIRE', KEYS[2], tonumber(ARGV[10]))
    end
end

local raw_message = ARGV[1]
local task_id = ARGV[2]
if redis.call('EXISTS', KEYS[2]) == 0 then
    return {'missing_task'}
end
if redis.call('HGET', KEYS[2], 'task_id') ~= task_id then
    return {'task_mismatch'}
end
local outcome = redis.call('HGET', KEYS[2], 'outcome') or ''
if outcome ~= '' and outcome ~= 'none' then
    return {'terminal', outcome}
end

local now = redis_now()
local deadline = tonumber(redis.call('HGET', KEYS[2], 'start_deadline_at') or '')
if deadline and now > deadline then
    local removed = redis.call('LREM', KEYS[1], 1, raw_message)
    if removed == 0 then
        return {'not_found'}
    end
    terminalize('skipped', 'deadline', 'start deadline missed', now, '0')
    release_lease()
    redis.call('HINCRBY', KEYS[8], 'outcome.skipped', 1)
    redis.call('HINCRBY', KEYS[8], 'deadline_missed', 1)
    return {'skipped', tostring(now)}
end

if ARGV[4] == '1' then
    local latest = redis.call('GET', KEYS[5])
    if latest and latest ~= ARGV[5] then
        local removed = redis.call('LREM', KEYS[1], 1, raw_message)
        if removed == 0 then
            return {'not_found'}
        end
        terminalize('cancelled', 'superseded', 'a newer supersede version exists', now, '0')
        release_lease()
        redis.call('HINCRBY', KEYS[8], 'outcome.cancelled', 1)
        redis.call('HINCRBY', KEYS[8], 'superseded', 1)
        return {'cancelled', tostring(now)}
    end
end

local attempt = tonumber(redis.call('HGET', KEYS[2], 'attempt') or '0')
local max_attempts = tonumber(redis.call('HGET', KEYS[2], 'max_attempts') or '1')
if attempt >= max_attempts then
    local removed = redis.call('LREM', KEYS[1], 1, raw_message)
    if removed == 0 then
        return {'not_found'}
    end
    redis.call('LPUSH', KEYS[3], raw_message)
    terminalize('failed', 'attempt_budget', 'attempt budget exhausted before start', now, '1')
    release_lease()
    redis.call('HINCRBY', KEYS[8], 'outcome.failed', 1)
    return {'failed_budget', tostring(attempt)}
end

local envelope = cjson.decode(raw_message)
if ARGV[6] == '1' then
    local acquired = redis.call('SET', KEYS[6], ARGV[7], 'NX', 'PX', tonumber(ARGV[8]))
    if not acquired then
        local run_at = now + tonumber(ARGV[9])
        envelope['available_at'] = run_at
        envelope['delay_reason'] = 'concurrency'
        envelope['lease_token'] = nil
        local deferred_message = cjson.encode(envelope)
        local removed = redis.call('LREM', KEYS[1], 1, raw_message)
        if removed == 0 then
            return {'not_found'}
        end
        redis.call('ZADD', KEYS[7], run_at, deferred_message)
        redis.call(
            'HSET', KEYS[2],
            'available_at', tostring(run_at),
            'delay_reason', 'concurrency',
            'updated_at', tostring(now)
        )
        redis.call('HINCRBY', KEYS[8], 'concurrency.deferred', 1)
        return {'deferred', deferred_message, tostring(run_at)}
    end
end

attempt = attempt + 1
envelope['attempt'] = attempt
if ARGV[6] == '1' then
    envelope['lease_token'] = ARGV[7]
end
local started_message = cjson.encode(envelope)
local removed = redis.call('LREM', KEYS[1], 1, raw_message)
if removed == 0 then
    release_lease()
    return {'not_found'}
end
redis.call('LPUSH', KEYS[1], started_message)
redis.call(
    'HSET', KEYS[2],
    'attempt', tostring(attempt),
    'last_started_at', tostring(now),
    'updated_at', tostring(now),
    'available_at', tostring(now),
    'delay_reason', ''
)
if not redis.call('HGET', KEYS[2], 'started_at') then
    redis.call('HSET', KEYS[2], 'started_at', tostring(now))
end
redis.call('HINCRBY', KEYS[8], 'attempt.started', 1)
redis.call('HINCRBY', KEYS[8], 'attempt.' .. tostring(attempt), 1)
local created_at = tonumber(redis.call('HGET', KEYS[2], 'created_at') or tostring(now))
redis.call('HINCRBYFLOAT', KEYS[8], 'queue_wait_seconds.total', math.max(now - created_at, 0))
redis.call('HINCRBY', KEYS[8], 'queue_wait_seconds.count', 1)
return {'started', started_message, tostring(attempt), tostring(now)}
"""


COMPLETE_TASK_LUA = r"""
local function key_type(key)
    local value = redis.call('TYPE', key)
    if type(value) == 'table' then return value['ok'] end
    return value
end
local function redis_now()
    local value = redis.call('TIME')
    return tonumber(value[1]) + tonumber(value[2]) / 1000000
end
local function finalize_owner(outcome, now)
    if ARGV[3] ~= '1' or key_type(KEYS[3]) ~= 'hash' then return end
    if redis.call('HGET', KEYS[3], 'task_id') ~= ARGV[2] then return end
    redis.call('HSET', KEYS[3], 'outcome', outcome)
    local retain_until = tonumber(redis.call('HGET', KEYS[3], 'dedup_until') or '')
    if retain_until and retain_until > now then
        redis.call('EXPIREAT', KEYS[3], math.ceil(retain_until))
    else
        redis.call('DEL', KEYS[3])
    end
end
local raw_message = ARGV[1]
local task_id = ARGV[2]
if redis.call('EXISTS', KEYS[2]) == 0 then return {'missing_task'} end
local outcome = redis.call('HGET', KEYS[2], 'outcome') or ''
if outcome ~= '' and outcome ~= 'none' then return {'terminal', outcome} end
local removed = redis.call('LREM', KEYS[1], 1, raw_message)
if removed == 0 then return {'not_found'} end
local now = redis_now()
redis.call(
    'HSET', KEYS[2],
    'outcome', 'completed',
    'status', 'completed',
    'finished_at', tostring(now),
    'updated_at', tostring(now),
    'operational_message', '0',
    'reason_code', '',
    'reason', ''
)
if ARGV[6] ~= '' and ARGV[7] == 'full' then
    redis.call('HSET', KEYS[2], 'result', ARGV[6])
end
redis.call('EXPIRE', KEYS[2], tonumber(ARGV[5]))
finalize_owner('completed', now)
if ARGV[4] ~= '' and redis.call('GET', KEYS[4]) == ARGV[4] then
    redis.call('DEL', KEYS[4])
end
redis.call('HINCRBY', KEYS[5], 'outcome.completed', 1)
return {'completed', tostring(now)}
"""


RETRY_TASK_LUA = r"""
local function key_type(key)
    local value = redis.call('TYPE', key)
    if type(value) == 'table' then return value['ok'] end
    return value
end
local function redis_now()
    local value = redis.call('TIME')
    return tonumber(value[1]) + tonumber(value[2]) / 1000000
end
local function finalize_owner(outcome, now)
    if ARGV[8] ~= '1' or key_type(KEYS[4]) ~= 'hash' then return end
    if redis.call('HGET', KEYS[4], 'task_id') ~= ARGV[3] then return end
    redis.call('HSET', KEYS[4], 'outcome', outcome)
    local retain_until = tonumber(redis.call('HGET', KEYS[4], 'dedup_until') or '')
    if retain_until and retain_until > now then
        redis.call('EXPIREAT', KEYS[4], math.ceil(retain_until))
    else
        redis.call('DEL', KEYS[4])
    end
end
local raw_message = ARGV[1]
local next_message = ARGV[2]
local task_id = ARGV[3]
if redis.call('EXISTS', KEYS[2]) == 0 then return {'missing_task'} end
local outcome = redis.call('HGET', KEYS[2], 'outcome') or ''
if outcome ~= '' and outcome ~= 'none' then return {'terminal', outcome} end
local run_at = tonumber(ARGV[4])
local deadline = tonumber(redis.call('HGET', KEYS[2], 'start_deadline_at') or '')
local now = redis_now()
if deadline and run_at >= deadline then
    local removed = redis.call('LREM', KEYS[1], 1, raw_message)
    if removed == 0 then return {'not_found'} end
    redis.call(
        'HSET', KEYS[2],
        'outcome', 'skipped',
        'status', 'skipped',
        'reason_code', 'deadline',
        'reason', 'next retry would miss start deadline',
        'finished_at', tostring(now),
        'updated_at', tostring(now),
        'operational_message', '0'
    )
    redis.call('EXPIRE', KEYS[2], tonumber(ARGV[7]))
    finalize_owner('skipped', now)
    if ARGV[9] ~= '' and redis.call('GET', KEYS[5]) == ARGV[9] then
        redis.call('DEL', KEYS[5])
    end
    redis.call('HINCRBY', KEYS[6], 'outcome.skipped', 1)
    redis.call('HINCRBY', KEYS[6], 'deadline_missed', 1)
    return {'skipped', tostring(now)}
end
local removed = redis.call('LREM', KEYS[1], 1, raw_message)
if removed == 0 then return {'not_found'} end
redis.call('ZADD', KEYS[3], run_at, next_message)
redis.call(
    'HSET', KEYS[2],
    'last_error_code', ARGV[5],
    'last_error', ARGV[6],
    'last_error_at', tostring(now),
    'available_at', tostring(run_at),
    'delay_reason', 'retry',
    'updated_at', tostring(now),
    'operational_message', '1'
)
if ARGV[9] ~= '' and redis.call('GET', KEYS[5]) == ARGV[9] then
    redis.call('DEL', KEYS[5])
end
redis.call('HINCRBY', KEYS[6], 'retry.scheduled', 1)
redis.call('HINCRBY', KEYS[6], 'retry.code.' .. ARGV[5], 1)
return {'retry', tostring(run_at)}
"""


FAIL_TASK_LUA = r"""
local function key_type(key)
    local value = redis.call('TYPE', key)
    if type(value) == 'table' then return value['ok'] end
    return value
end
local function redis_now()
    local value = redis.call('TIME')
    return tonumber(value[1]) + tonumber(value[2]) / 1000000
end
local function finalize_owner(outcome, now)
    if ARGV[6] ~= '1' or key_type(KEYS[4]) ~= 'hash' then return end
    if redis.call('HGET', KEYS[4], 'task_id') ~= ARGV[3] then return end
    redis.call('HSET', KEYS[4], 'outcome', outcome)
    local retain_until = tonumber(redis.call('HGET', KEYS[4], 'dedup_until') or '')
    if retain_until and retain_until > now then
        redis.call('EXPIREAT', KEYS[4], math.ceil(retain_until))
    else
        redis.call('DEL', KEYS[4])
    end
end
local raw_message = ARGV[1]
local dlq_message = ARGV[2]
local task_id = ARGV[3]
if redis.call('EXISTS', KEYS[2]) == 0 then return {'missing_task'} end
local outcome = redis.call('HGET', KEYS[2], 'outcome') or ''
if outcome ~= '' and outcome ~= 'none' then return {'terminal', outcome} end
local removed = redis.call('LREM', KEYS[1], 1, raw_message)
if removed == 0 then return {'not_found'} end
redis.call('LPUSH', KEYS[3], dlq_message)
local now = redis_now()
redis.call(
    'HSET', KEYS[2],
    'outcome', 'failed',
    'status', 'failed',
    'reason_code', ARGV[4],
    'reason', ARGV[5],
    'last_error_code', ARGV[4],
    'last_error', ARGV[5],
    'last_error_at', tostring(now),
    'finished_at', tostring(now),
    'updated_at', tostring(now),
    'operational_message', '1'
)
finalize_owner('failed', now)
if ARGV[7] ~= '' and redis.call('GET', KEYS[5]) == ARGV[7] then
    redis.call('DEL', KEYS[5])
end
redis.call('HINCRBY', KEYS[6], 'outcome.failed', 1)
redis.call('HINCRBY', KEYS[6], 'failure.code.' .. ARGV[4], 1)
return {'failed', tostring(now)}
"""


CANCEL_OR_PURGE_LUA = r"""
local function key_type(key)
    local value = redis.call('TYPE', key)
    if type(value) == 'table' then return value['ok'] end
    return value
end
local function redis_now()
    local value = redis.call('TIME')
    return tonumber(value[1]) + tonumber(value[2]) / 1000000
end
local source_type = ARGV[1]
local raw_message = ARGV[2]
local task_id = ARGV[3]
local identity_policy = ARGV[4]
local has_owner = ARGV[5] == '1'
local removed = 0
if source_type == 'zset' then
    removed = redis.call('ZREM', KEYS[1], raw_message)
else
    removed = redis.call('LREM', KEYS[1], 1, raw_message)
end
if removed == 0 then return {'not_found'} end

local now = redis_now()
local outcome = redis.call('HGET', KEYS[2], 'outcome') or ''
if outcome == '' or outcome == 'none' then
    outcome = 'cancelled'
    redis.call(
        'HSET', KEYS[2],
        'outcome', outcome,
        'status', outcome,
        'reason_code', ARGV[7],
        'reason', ARGV[8],
        'finished_at', tostring(now)
    )
end
redis.call(
    'HSET', KEYS[2],
    'operational_message', '0',
    'updated_at', tostring(now),
    'purged_at', tostring(now)
)
redis.call('EXPIRE', KEYS[2], tonumber(ARGV[6]))

if has_owner and key_type(KEYS[3]) == 'hash' and redis.call('HGET', KEYS[3], 'task_id') == task_id then
    if identity_policy == 'release' then
        redis.call('DEL', KEYS[3])
    else
        redis.call('HSET', KEYS[3], 'outcome', outcome)
        local retain_until = tonumber(redis.call('HGET', KEYS[3], 'dedup_until') or '')
        if retain_until and retain_until > now then
            redis.call('EXPIREAT', KEYS[3], math.ceil(retain_until))
        else
            redis.call('DEL', KEYS[3])
        end
    end
end
if ARGV[9] ~= '' and redis.call('GET', KEYS[4]) == ARGV[9] then
    redis.call('DEL', KEYS[4])
end
redis.call('HINCRBY', KEYS[5], 'admin.purged', 1)
if outcome == 'cancelled' then
    redis.call('HINCRBY', KEYS[5], 'outcome.cancelled', 1)
end
return {'removed', outcome, tostring(now)}
"""


ADMIN_MOVE_LUA = r"""
local source_type = ARGV[1]
local raw_message = ARGV[2]
local destination_message = ARGV[3]
local task_id = ARGV[4]
if redis.call('EXISTS', KEYS[3]) == 1 then
    local outcome = redis.call('HGET', KEYS[3], 'outcome') or ''
    if outcome ~= '' and outcome ~= 'none' then
        return {'terminal', outcome}
    end
end
local removed = 0
if source_type == 'zset' then
    removed = redis.call('ZREM', KEYS[1], raw_message)
else
    removed = redis.call('LREM', KEYS[1], 1, raw_message)
end
if removed == 0 then return {'not_found'} end
redis.call('LPUSH', KEYS[2], destination_message)
if redis.call('EXISTS', KEYS[3]) == 1 then
    local value = redis.call('TIME')
    local now = tonumber(value[1]) + tonumber(value[2]) / 1000000
    redis.call(
        'HSET', KEYS[3],
        'operational_message', '1',
        'manual_moved_at', tostring(now),
        'updated_at', tostring(now)
    )
end
return {'moved', task_id}
"""


MOVE_DUE_DELAY_LUA = r"""
local value = redis.call('TIME')
local now = tonumber(value[1]) + tonumber(value[2]) / 1000000
local count = 0
local limit = tonumber(ARGV[1])
while count < limit do
    local tasks = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', now, 'LIMIT', 0, 1)
    if #tasks == 0 then break end
    local task = tasks[1]
    if redis.call('ZREM', KEYS[1], task) == 1 then
        redis.call('LPUSH', KEYS[2], task)
        count = count + 1
    end
end
return count
"""


RENEW_LEASE_LUA = r"""
if redis.call('GET', KEYS[1]) ~= ARGV[1] then
    return 0
end
redis.call('PEXPIRE', KEYS[1], tonumber(ARGV[2]))
return 1
"""


RELEASE_LEASE_LUA = r"""
if redis.call('GET', KEYS[1]) ~= ARGV[1] then
    return 0
end
return redis.call('DEL', KEYS[1])
"""


REPLAY_TASK_LUA = r"""
local function key_type(key)
    local value = redis.call('TYPE', key)
    if type(value) == 'table' then return value['ok'] end
    return value
end
local function redis_now()
    local value = redis.call('TIME')
    return tonumber(value[1]) + tonumber(value[2]) / 1000000
end
local function owner_info(key)
    local kind = key_type(key)
    if kind == 'hash' then
        return redis.call('HGET', key, 'task_id') or '',
               redis.call('HGET', key, 'outcome') or 'none',
               tonumber(redis.call('HGET', key, 'generation') or '0')
    elseif kind == 'string' then
        return redis.call('GET', key) or '', 'none', 0
    end
    return '', '', 0
end
local function supersede_is_newer(candidate, current)
    if not current or current == '' then return true end
    local candidate_kind = string.sub(candidate, 1, 2)
    local current_kind = string.sub(current, 1, 2)
    if candidate_kind == 'i:' and current_kind == 'i:' then
        return tonumber(string.sub(candidate, 3)) > tonumber(string.sub(current, 3))
    end
    if candidate_kind == current_kind then
        return string.sub(candidate, 3) > string.sub(current, 3)
    end
    return candidate > current
end

local source_message = ARGV[1]
local require_source = ARGV[2] == '1'
local old_task_id = ARGV[3]
local new_task_id = ARGV[4]
local has_identity = ARGV[5] == '1'
local logical_key = ARGV[6]
local dedup_until = ARGV[7]
local new_message = ARGV[8]
local available_at = tonumber(ARGV[9])
local record = cjson.decode(ARGV[10])
local queue_name = ARGV[11]
local has_supersede = ARGV[12] == '1'
local supersede_value = ARGV[13]
local created_at = tonumber(ARGV[14])
local ttl_seconds = tonumber(ARGV[15])

if redis.call('EXISTS', KEYS[6]) == 0 then return {'missing_source'} end
local old_outcome = redis.call('HGET', KEYS[6], 'outcome') or redis.call('HGET', KEYS[6], 'status') or ''
if old_outcome ~= 'failed' and old_outcome ~= 'skipped' and old_outcome ~= 'cancelled' then
    return {'not_replayable', old_outcome}
end
if redis.call('EXISTS', KEYS[7]) == 1 then return {'task_id_collision'} end

local generation = 1
if has_identity then
    local owner_id, owner_outcome, owner_generation = owner_info(KEYS[4])
    if owner_id == '' then
        owner_id, owner_outcome, owner_generation = owner_info(KEYS[5])
    end
    if owner_id ~= '' and owner_id ~= old_task_id then
        local reason = 'duplicate_active'
        if owner_outcome ~= '' and owner_outcome ~= 'none' then reason = 'duplicate_retained' end
        redis.call('HINCRBY', KEYS[11], 'replay.' .. reason, 1)
        return {reason, owner_id}
    end
    generation = math.max(owner_generation + 1, 1)
end

if require_source then
    local removed = redis.call('LREM', KEYS[1], 1, source_message)
    if removed == 0 then return {'source_not_found'} end
end

local now = redis_now()
redis.call(
    'HSET', KEYS[6],
    'operational_message', '0',
    'replayed_by', new_task_id,
    'updated_at', tostring(now)
)
redis.call('EXPIRE', KEYS[6], ttl_seconds)

if has_identity then
    if key_type(KEYS[4]) ~= 'none' then redis.call('DEL', KEYS[4]) end
    if KEYS[5] ~= KEYS[4] and key_type(KEYS[5]) ~= 'none' then redis.call('DEL', KEYS[5]) end
    redis.call(
        'HSET', KEYS[4],
        'task_id', new_task_id,
        'logical_key', logical_key,
        'generation', tostring(generation),
        'outcome', 'none',
        'dedup_until', dedup_until
    )
    redis.call('PERSIST', KEYS[4])
end

for field, value in pairs(record) do
    redis.call('HSET', KEYS[7], field, tostring(value))
end
redis.call('HSET', KEYS[7], 'generation', tostring(generation))
redis.call('ZADD', KEYS[8], created_at, new_task_id)
redis.call('ZADD', KEYS[9], now, queue_name)

if has_supersede then
    local current = redis.call('GET', KEYS[10])
    if supersede_is_newer(supersede_value, current) then
        redis.call('SET', KEYS[10], supersede_value)
    end
end

local location = 'ready'
if available_at > now then
    redis.call('ZADD', KEYS[3], available_at, new_message)
    location = 'delay'
else
    redis.call('LPUSH', KEYS[2], new_message)
end
redis.call('HINCRBY', KEYS[11], 'replay.accepted', 1)
redis.call('HINCRBY', KEYS[11], 'enqueue.accepted', 1)
return {'enqueued', new_task_id, old_task_id, tostring(generation), location}
"""
