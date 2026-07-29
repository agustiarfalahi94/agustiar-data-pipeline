select
    region,
    fetch_timestamp,
    vehicles_received,
    vehicles_rejected,
    vehicles_inserted,
    avg_data_lag_seconds,
    max_data_lag_seconds,
    total_dropout,
    {{ reliability_score(
        'case when vehicles_received > 0 then vehicles_inserted::double / vehicles_received else 0 end',
        'case when total_dropout then 0.0 else 1.0 end',
        'avg_data_lag_seconds'
    ) }} as reliability_score
from {{ ref('stg_fetch_quality') }}
