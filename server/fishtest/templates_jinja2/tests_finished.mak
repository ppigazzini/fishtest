{% extends "base.mak" %}

{% block title %}Finished Tests{{ title_suffix }} | Stockfish Testing{% endblock %}

{% block body %}
{% set success_only = filters.success_only if filters is defined else false %}
{% set yellow_only = filters.yellow_only if filters is defined else false %}
{% set ltc_only = filters.ltc_only if filters is defined else false %}
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
  title_text=title_text,
  toggle=None,
  toggle_state='Show',
  page_idx=0,
  username="",
  show_gauge=show_gauge
%}
  {% include "run_table.mak" %}
{% endwith %}
{% endblock %}
