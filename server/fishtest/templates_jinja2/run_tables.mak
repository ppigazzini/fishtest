{% if username is defined %}
  {% set prefix = run_tables_prefix(username) %}
{% else %}
  {% set prefix = '' %}
{% endif %}

{% if page_idx == 0 %}
  {% set pending_approval_runs = runs['pending'] | selectattr('approved', 'equalto', False) | list %}
  {% set paused_runs = runs['pending'] | selectattr('approved', 'equalto', True) | list %}

  {% with
    runs=pending_approval_runs,
    show_delete=True,
    header='Pending approval',
    count=pending_approval_runs | length,
    toggle=prefix ~ 'pending',
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
    alt='No paused tests'
  %}
    {% include "run_table.mak" %}
  {% endwith %}

  {% with
    runs=failed_runs,
    show_delete=True,
    toggle=prefix ~ 'failed',
    count=failed_runs | length,
    header='Failed',
    alt='No failed tests on this page'
  %}
    {% include "run_table.mak" %}
  {% endwith %}

  {% with
    runs=runs['active'],
    header='Active',
    toggle=prefix ~ 'active',
    count=runs['active'] | length,
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
  pages=finished_runs_pages
%}
  {% include "run_table.mak" %}
{% endwith %}
