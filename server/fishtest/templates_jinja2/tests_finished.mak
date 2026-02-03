{% extends "base.mak" %}

{% block body %}
{% set title = "" %}
{% if "ltc_only" in request.url %}
  {% set title = title ~ " - LTC" %}
{% endif %}
{% if "success_only" in request.url %}
  {% set title = title ~ " - Greens" %}
{% endif %}
{% if "yellow_only" in request.url %}
  {% set title = title ~ " - Yellows" %}
{% endif %}

<script>
  document.title =  "Finishes Test{{ title }} | Stockfish Testing";
</script>

<h2>
  Finished Tests
  {% if 'success_only' in request.url %}
    - Greens
  {% elif 'yellow_only' in request.url %}
    - Yellows
  {% elif 'ltc_only' in request.url %}
    - LTC
  {% endif %}
</h2>

{% with
  runs=finished_runs,
  header='Finished',
  count=num_finished_runs,
  pages=finished_runs_pages,
  title=title,
  toggle=None
%}
  {% include "run_table.mak" %}
{% endwith %}
{% endblock %}
