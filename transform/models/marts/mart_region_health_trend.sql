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
    case when is_scoreable then
        {{ reliability_score(
            'case when vehicles_received > 0 then vehicles_inserted::double / vehicles_received else 0 end',
            "case when fetch_status = 'ERROR' then 0.0 else 1.0 end",
            'avg_data_lag_seconds'
        ) }}
    end as reliability_score
from {{ ref('stg_fetch_quality') }}
