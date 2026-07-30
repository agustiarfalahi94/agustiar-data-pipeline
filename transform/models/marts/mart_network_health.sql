{% set cutoff = "cast(epoch(now()) as bigint) - cast(" ~ var('health_window_hours') ~ " as bigint) * 3600" %}

with q as (
    select * from {{ ref('stg_fetch_quality') }}
    where fetch_timestamp >= {{ cutoff }}
),
agg as (
    select
        region,
        count(*)                                              as total_fetches,
        sum(case when is_scoreable then 1 else 0 end)         as scoreable_fetches,
        sum(case when total_dropout then 1 else 0 end)        as dropout_count,
        coalesce(avg(case when vehicles_received > 0
            then vehicles_inserted::double / vehicles_received end), 0) as reporting_rate,
        -- Only a genuine ERROR counts against availability. EMPTY means the feed
        -- answered correctly and no service was running.
        sum(case when is_scoreable and fetch_status = 'ERROR' then 1 else 0 end) as error_count,
        coalesce(avg(avg_data_lag_seconds), 0)                as avg_data_lag_seconds,
        max(fetch_timestamp)                                  as last_fetch_timestamp
    from q
    group by region
),
scored as (
    select
        *,
        scoreable_fetches = 0 as feed_unavailable,
        case when scoreable_fetches > 0
             then 1.0 - error_count::double / scoreable_fetches
        end as availability
    from agg
)
select
    region,
    total_fetches,
    scoreable_fetches,
    feed_unavailable,
    dropout_count,
    reporting_rate,
    availability,
    avg_data_lag_seconds,
    last_fetch_timestamp,
    case when feed_unavailable then null else
        {{ reliability_score('reporting_rate', 'availability', 'avg_data_lag_seconds') }}
    end as reliability_score
from scored
order by reliability_score desc nulls last
