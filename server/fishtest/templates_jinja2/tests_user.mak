{% extends "base.mak" %}

{% block title %}{{ username }} | Stockfish Testing{% endblock %}

{% block body %}
<h2>
{% if is_approver %}
  <a href="{{ urls.user_profile }}">{{ username }}</a> - Tests
{% else %}
  {{ username }} - Tests
{% endif %}
</h2>

{% with
  runs=run_tables_ctx.runs,
  failed_runs=run_tables_ctx.failed_runs,
  finished_runs=run_tables_ctx.finished_runs,
  num_finished_runs=run_tables_ctx.num_finished_runs,
  finished_runs_pages=run_tables_ctx.finished_runs_pages,
  page_idx=run_tables_ctx.page_idx,
  prefix=run_tables_ctx.prefix
%}
  {% include "run_tables.mak" %}
{% endwith %}
{% endblock %}
