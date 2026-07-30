{% macro reliability_score(reporting_rate, availability, avg_lag_seconds) %}
round((
      0.4 * ({{ reporting_rate }})
    + 0.4 * ({{ availability }})
    + 0.2 * greatest(0.0, 1.0 - ({{ avg_lag_seconds }}) / 300.0)
) * 100)
{% endmacro %}


{#
    The same score with the reporting-rate term removed and the two remaining
    weights renormalised back to 1.0.

    Used when nothing was received, so `vehicles_inserted / vehicles_received`
    is undefined rather than zero. Substituting a zero would punish a feed that
    answered correctly with no service running; dropping the term scores the
    cycle on what it can actually tell us. This is also exactly what the
    aggregate does when `avg()` skips a NULL reporting rate, which is what keeps
    `mart_network_health` and `mart_region_health_trend` telling the same story.
#}
{% macro reliability_score_without_reporting(availability, avg_lag_seconds) %}
round((
      0.4 * ({{ availability }})
    + 0.2 * greatest(0.0, 1.0 - ({{ avg_lag_seconds }}) / 300.0)
) / 0.6 * 100)
{% endmacro %}
