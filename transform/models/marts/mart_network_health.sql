{% set cutoff = "cast(epoch(now()) as bigint) - cast(" ~ var('health_window_hours') ~ " as bigint) * 3600" %}

with q as (
    select * from {{ ref('stg_fetch_quality') }}
    where fetch_timestamp >= {{ cutoff }}
),
agg as (
    select
        region,
        count(*)                                             as total_fetches,
        sum(case when total_dropout then 1 else 0 end)       as dropout_count,
        coalesce(avg(case when vehicles_received > 0
            then vehicles_inserted::double / vehicles_received end), 0) as reporting_rate,
        1.0 - sum(case when total_dropout then 1 else 0 end)::double
              / count(*)                                     as availability,
        coalesce(avg(avg_data_lag_seconds), 0)               as avg_data_lag_seconds,
        max(fetch_timestamp)                                 as last_fetch_timestamp
    from q
    group by region
)
select
    region,
    total_fetches,
    dropout_count,
    reporting_rate,
    availability,
    avg_data_lag_seconds,
    last_fetch_timestamp,
    {{ reliability_score('reporting_rate', 'availability', 'avg_data_lag_seconds') }} as reliability_score
from agg
order by reliability_score desc
