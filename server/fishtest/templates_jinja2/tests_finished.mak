{% extends "base.mak" %}

{% block title %}Finished Tests{{ title_suffix }} | Stockfish Testing{% endblock %}

{% block body %}
{% if filters is defined %}
  {% set success_only = filters.success_only %}
  {% set yellow_only = filters.yellow_only %}
  {% set ltc_only = filters.ltc_only %}
{% else %}
  {% set success_only = 'success_only' in request.url %}
  {% set yellow_only = 'yellow_only' in request.url %}
  {% set ltc_only = 'ltc_only' in request.url %}
{% endif %}
<h2>
  Finished Tests
  {% if success_only %}
    - Greens
  {% elif yellow_only %}
    - Yellows
  {% elif ltc_only %}
    - LTC
  {% endif %}
</h2>

{% with
  runs=finished_runs,
  header='Finished',
  count=num_finished_runs,
  pages=finished_runs_pages,
  title_suffix=title_suffix,
  toggle=None,
  toggle_state='Show',
  page_idx=0,
  username=""
%}
  {% include "run_table.mak" %}
{% endwith %}
{% endblock %}
