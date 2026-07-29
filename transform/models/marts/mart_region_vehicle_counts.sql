-- cast the var to bigint: with widened CI vars, days * 86400 overflows DuckDB's INT32 literal math
{% set cutoff = "cast(epoch(now()) as bigint) - cast(" ~ var('retention_days') ~ " as bigint) * 86400" %}

select
    region,
    count(distinct vehicle_id) as unique_vehicles
from {{ ref('stg_vehicle_positions') }}
where insert_timestamp >= {{ cutoff }}
group by region
order by unique_vehicles desc
