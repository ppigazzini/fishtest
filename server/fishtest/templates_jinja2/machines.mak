{% macro clip_long(text, max_length=20) -%}
  {%- if text|length > max_length -%}
    {{ text[:max_length] ~ "..." }}
  {%- else -%}
    {{ text }}
  {%- endif -%}
{%- endmacro %}

<table class="table table-striped table-sm">
  <thead class="sticky-top">
    <tr>
      <th>Machine</th>
      <th>Cores</th>
      <th>UUID</th>
      <th>MNps</th>
      <th>RAM</th>
      <th>System</th>
      <th>Arch</th>
      <th>Compiler</th>
      <th>Python</th>
      <th>Worker</th>
      <th>Running on</th>
      <th>Last active</th>
    </tr>
  </thead>
  <tbody>
    {% for machine in machines_list %}
      {% set gcc_version = machine['gcc_version'] | map('string') | join('.') %}
      {% set compiler = machine.get('compiler', 'g++') %}
      {% set python_version = machine['python_version'] | map('string') | join('.') %}
      {% set version = machine['version'] ~ ('*' * machine['modified']) %}
      {% set worker_name_ = worker_name(machine, short=True) %}
      {% set formatted_time_ago = format_time_ago(machine['last_updated']) %}
      {% set sort_value_time_ago = -machine['last_updated'].timestamp() %}
      {% set branch = machine['run']['args']['new_tag'] %}
      {% set task_id = machine['task_id'] | string %}
      {% set run_id = machine['run']['_id'] | string %}
      <tr>
        <td>{{ machine['username'] }}</td>
        <td>
          {% if 'country_code' in machine %}
            <div class="flag flag-{{ machine['country_code'].lower() }}"
                 style="display: inline-block"></div>
          {% endif %}
          {{ machine['concurrency'] }}
        </td>
        <td><a href="/workers/{{ worker_name_ }}">{{ machine['unique_key'].split('-')[0] }}</a></td>
        <td>{{ "{0:.2f}".format(machine['nps'] / 1000000) }}</td>
        <td>{{ machine['max_memory'] }}</td>
        <td>{{ machine['uname'] }}</td>
        <td>{{ machine['worker_arch'] }}</td>
        <td>{{ compiler }} {{ gcc_version }}</td>
        <td>{{ python_version }}</td>
        <td>{{ version }}</td>
        <td>
          <a href="/tests/view/{{ run_id + '?show_task=' + task_id }}" title="{{ branch + '/' + task_id }}">{{ clip_long(branch) + '/' + task_id }}</a>
        </td>
        <td data-sort-value="{{ sort_value_time_ago }}">{{ formatted_time_ago }}</td>
      </tr>
    {% else %}
      <tr id="no-machines">
        <td colspan=20>No machines running</td>
      </tr>
    {% endfor %}
  </tbody>
</table>
