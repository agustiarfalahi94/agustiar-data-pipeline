with source as (
    select * from {{ source('transit', 'live_buses') }}
)
select
    region,
    cast(vehicle_id as varchar)            as vehicle_id,
    cast(latitude as double)               as latitude,
    cast(longitude as double)              as longitude,
    coalesce(cast(bearing as double), 0)   as bearing,
    least(round(coalesce(cast(speed as double), 0) * 3.6), 120) as speed_kmh,
    cast(timestamp as bigint)              as timestamp,
    cast(insert_timestamp as bigint)       as insert_timestamp
from source
where latitude is not null
  and longitude is not null
  and latitude <> 0
  and longitude <> 0
