with source as (
    select * from {{ source('transit', 'fetch_quality_log') }}
)
select
    cast(fetch_timestamp as bigint)         as fetch_timestamp,
    region,
    cast(vehicles_received as integer)      as vehicles_received,
    cast(vehicles_rejected as integer)      as vehicles_rejected,
    cast(vehicles_inserted as integer)      as vehicles_inserted,
    cast(avg_data_lag_seconds as double)    as avg_data_lag_seconds,
    cast(max_data_lag_seconds as double)    as max_data_lag_seconds,
    cast(total_dropout as boolean)          as total_dropout,
    cast(fetch_duration_ms as integer)      as fetch_duration_ms,
    -- Rows written before fetch_status existed are treated as OK so historical
    -- data keeps scoring exactly as it did.
    coalesce(fetch_status, 'OK')            as fetch_status,
    -- NO_FEED (withdrawn upstream) and THROTTLED (our own rate limiting) say
    -- nothing about the agency, so they are excluded from scoring entirely.
    coalesce(fetch_status, 'OK') not in ('NO_FEED', 'THROTTLED') as is_scoreable
from source
