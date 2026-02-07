{% extends "base.mak" %}

{% block title %}Finished Tests{{ title_suffix }} | Stockfish Testing{% endblock %}

{% block body %}
<h2>
  Finished Tests
  {% if filters.success_only %}
    - Greens
  {% elif filters.yellow_only %}
    - Yellows
  {% elif filters.ltc_only %}
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
