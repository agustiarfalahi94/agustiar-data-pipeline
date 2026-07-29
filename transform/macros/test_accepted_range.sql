{#
    Generic test: assert every non-null value of `column_name` falls within
    [min_value, max_value]. Both bounds are optional, so `min_value`-only
    usages are supported. `inclusive` (default true) treats the bounds as
    valid values.

    Local replacement for `dbt_utils.accepted_range`, so the project has no
    external dbt package dependency and therefore parses on a fresh clone
    without a `dbt deps` step.

    A generic test passes when it returns zero rows, so this returns the
    offending rows.
#}
{% test accepted_range(model, column_name, min_value=none, max_value=none, inclusive=true) %}

with validation as (
    select {{ column_name }} as value_field
    from {{ model }}
)

select value_field
from validation
where false
{% if min_value is not none %}
    or value_field {{ '<' if inclusive else '<=' }} {{ min_value }}
{% endif %}
{% if max_value is not none %}
    or value_field {{ '>' if inclusive else '>=' }} {{ max_value }}
{% endif %}

{% endtest %}
