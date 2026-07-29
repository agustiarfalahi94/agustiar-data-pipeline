{#
    Generic test: assert the combination of `combination_of_columns` is unique
    across the model — i.e. it is a valid grain / composite key.

    Declared at the model level:

        tests:
          - unique_combination_of_columns:
              combination_of_columns: [region, fetch_timestamp]

    A generic test passes when it returns zero rows, so this returns the
    duplicated key groups.
#}
{% test unique_combination_of_columns(model, combination_of_columns) %}

{%- set column_list = combination_of_columns | join(', ') -%}

select
    {{ column_list }},
    count(*) as n_records
from {{ model }}
group by {{ column_list }}
having count(*) > 1

{% endtest %}
