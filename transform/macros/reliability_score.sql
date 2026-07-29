{% macro reliability_score(reporting_rate, availability, avg_lag_seconds) %}
round((
      0.4 * ({{ reporting_rate }})
    + 0.4 * ({{ availability }})
    + 0.2 * greatest(0.0, 1.0 - ({{ avg_lag_seconds }}) / 300.0)
) * 100)
{% endmacro %}
