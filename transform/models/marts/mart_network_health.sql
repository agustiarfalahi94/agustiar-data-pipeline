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
        -- Dropouts are counted over scoreable rows only. The scorecard shows
        -- this number next to the score, so counting cycles the score itself
        -- ignores (NO_FEED/THROTTLED) would make the card contradict itself.
        sum(case when is_scoreable and total_dropout
            then 1 else 0 end)                                as dropout_count,
        -- Kept apart so the "feed unavailable" card can name the real cause
        -- instead of asserting "withdrawn upstream" for a rate-limited feed.
        sum(case when fetch_status = 'NO_FEED' then 1 else 0 end)   as no_feed_count,
        sum(case when fetch_status = 'THROTTLED' then 1 else 0 end) as throttled_count,
        -- NULL - not 0 - when no fetch in the window received anything: the
        -- reporting rate is undefined, and `scored` renormalises the score
        -- rather than scoring the region on a fabricated zero.
        avg(case when vehicles_received > 0
            then vehicles_inserted::double / vehicles_received end) as reporting_rate,
        -- Only a genuine ERROR counts against availability. EMPTY means the feed
        -- answered correctly and no service was running. (ERROR is always
        -- scoreable by construction, so no extra is_scoreable guard is needed.)
        sum(case when fetch_status = 'ERROR' then 1 else 0 end) as error_count,
        -- Averaged over scoreable rows only, for the same reason the score is:
        -- a NO_FEED cycle logs a lag of 0 it never measured, which would
        -- otherwise flatter the region's freshness term.
        coalesce(avg(case when is_scoreable
            then avg_data_lag_seconds end), 0)                as avg_data_lag_seconds,
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
    no_feed_count,
    throttled_count,
    dropout_count,
    reporting_rate,
    availability,
    avg_data_lag_seconds,
    last_fetch_timestamp,
    case
        when feed_unavailable then null
        -- No cycle received anything: score on the terms that apply, exactly
        -- as `mart_region_health_trend` does for such a cycle.
        when reporting_rate is null then
            {{ reliability_score_without_reporting('availability', 'avg_data_lag_seconds') }}
        else
            {{ reliability_score('reporting_rate', 'availability', 'avg_data_lag_seconds') }}
    end as reliability_score
from scored
order by reliability_score desc nulls last
