{% set availability = "case when fetch_status = 'ERROR' then 0.0 else 1.0 end" %}

select
    region,
    fetch_timestamp,
    vehicles_received,
    vehicles_rejected,
    vehicles_inserted,
    avg_data_lag_seconds,
    max_data_lag_seconds,
    total_dropout,
    -- Rows that aren't scoreable (NO_FEED/THROTTLED) say nothing about the
    -- agency, so they're absent from the trend line rather than plotted as a
    -- zero. Of the scoreable rows, only ERROR counts against availability -
    -- EMPTY (feed healthy, no service running) is not a dropout for scoring.
    --
    -- A cycle that received nothing has no reporting rate at all, so it is
    -- scored on the terms that do apply (renormalised) rather than on a
    -- fabricated zero. That mirrors `mart_network_health`, where avg() simply
    -- skips the NULL - without it the aggregate and this trend line disagree
    -- about the very same window (aggregate 100 vs a sparkline dipping to 60).
    case when is_scoreable then
        case when vehicles_received > 0 then
            {{ reliability_score(
                'vehicles_inserted::double / vehicles_received',
                availability,
                'avg_data_lag_seconds'
            ) }}
        else
            {{ reliability_score_without_reporting(
                availability,
                'avg_data_lag_seconds'
            ) }}
        end
    end as reliability_score
from {{ ref('stg_fetch_quality') }}
