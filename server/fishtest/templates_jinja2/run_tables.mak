{% if page_idx == 0 %}
  {% set pending_runs = runs.pending_approval %}
  {% set paused_runs = runs.paused %}
  {% set failed_runs_list = failed_runs %}
  {% set active_runs = runs.active %}
  {% with
    runs=pending_runs,
    show_delete=True,
    header='Pending approval',
    count=pending_runs | length,
    toggle=prefix ~ 'pending',
    toggle_state=runs.toggle_states.pending,
    alt='No tests pending approval'
  %}
    {% include "run_table.mak" %}
  {% endwith %}

  {% with
    runs=paused_runs,
    show_delete=True,
    header='Paused',
    count=paused_runs | length,
    toggle=prefix ~ 'paused',
    toggle_state=runs.toggle_states.paused,
    alt='No paused tests'
  %}
    {% include "run_table.mak" %}
  {% endwith %}

  {% with
    runs=failed_runs_list,
    show_delete=True,
    toggle=prefix ~ 'failed',
    toggle_state=runs.toggle_states.failed,
    count=failed_runs_list | length,
    header='Failed',
    alt='No failed tests on this page'
  %}
    {% include "run_table.mak" %}
  {% endwith %}

  {% with
    runs=active_runs,
    header='Active',
    toggle=prefix ~ 'active',
    toggle_state=runs.toggle_states.active,
    count=active_runs | length,
    alt='No active tests'
  %}
    {% include "run_table.mak" %}
  {% endwith %}
{% endif %}

{% with
  runs=finished_runs,
  header='Finished',
  count=num_finished_runs,
  toggle=(prefix ~ 'finished') if page_idx == 0 else None,
  toggle_state=runs.toggle_states.finished if page_idx == 0 else 'Show',
  pages=finished_runs_pages
%}
  {% include "run_table.mak" %}
{% endwith %}
