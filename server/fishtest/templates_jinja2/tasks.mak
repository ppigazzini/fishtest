{% for task in run['tasks'] + run.get('bad_tasks', []) %}
  {% set idx = loop.index0 %}
  {% if not ('bad' in task and idx < (run['tasks'] | length)) %}
    {% if 'stats' in task %}
      {% set task_id = task.get('task_id', idx) %}
      {% set stats = task.get('stats', {}) %}
      {% set total = stats['wins'] + stats['losses'] + stats['draws'] %}

      {% if task_id == show_task %}
        {% set active_style = 'highlight' %}
      {% elif task['active'] %}
        {% set active_style = 'info' %}
      {% else %}
        {% set active_style = '' %}
      {% endif %}
      {% set worker_info = task.get('worker_info', {}) %}
      {% set worker_username = worker_info.get('username', 'Unknown_worker') %}
      <tr class="{{ active_style }}" id=task{{ task_id }}>
        <td>
          <a href={{ "/api/pgn/{0}-{1:d}.pgn".format(run['_id'], task_id) }}>{{ task_id }}</a>
        </td>
        {% if 'bad' in task %}
          <td style="text-decoration:line-through; background-color:#ffebeb">
        {% else %}
          <td>
        {% endif %}
        {% if approver and worker_username != "Unknown_worker" %}
          <a href="/workers/{{ worker_name(worker_info, short=True) }}">
            {{ worker_name(worker_info) }}
          </a>
        {% elif worker_info %}
          {{ worker_name(worker_info) }}
        {% else %}
          -
        {% endif %}
        </td>
        <td>
          {% set gcc_version = worker_info.get('gcc_version', []) | map('string') | join('.') %}
          {% set compiler = worker_info.get('compiler', 'g++') %}
          {% set python_version = worker_info.get('python_version', []) | map('string') | join('.') %}
          {% set version = worker_info.get('version', '-') %}
          {% set ARCH = worker_info.get('ARCH', '-') %}
          {% set worker_arch = worker_info.get('worker_arch', "unknown") %}
          os: {{ worker_info.get('uname', '-') }};
          ram: {{ worker_info.get('max_memory', '-') }}MiB;
          compiler: {{ compiler }} {{ gcc_version }};
          python: {{ python_version }};
          worker: {{ version }};
          arch: {{ worker_arch }};
          features: {{ ARCH }}
        </td>
        <td>{{ (task.get('last_updated', '-') | string).split('.')[0] }}</td>
        <td>{{ "{0:03d} / {1:03d}".format(total, task['num_games']) }}</td>
        {% if 'pentanomial' not in run['results'] %}
          <td>{{ stats.get('wins', '-') }}</td>
          <td>{{ stats.get('losses', '-') }}</td>
          <td>{{ stats.get('draws', '-') }}</td>
        {% else %}
          {% set p = stats.get('pentanomial', [0] * 5) %}
          <td>[{{ p[0] }},&nbsp;{{ p[1] }},&nbsp;{{ p[2] }},&nbsp;{{ p[3] }},&nbsp;{{ p[4] }}]</td>
        {% endif %}
        <td>{{ stats.get('crashes', '-') }}</td>
        <td>{{ stats.get('time_losses', '-') }}</td>

        {% if 'spsa' not in run['args'] %}
          {% set d = display_residual(task, chi2) %}
          {% if d['residual'] != math.inf %}
            <td style="background-color:{{ d['display_color'] }}">
              {{ "{0:.3f}".format(d['residual']) }}
            </td>
          {% else %}
            <td>-</td>
          {% endif %}
        {% endif %}
      </tr>
    {% endif %}
  {% endif %}
{% endfor %}

{% if ((run['tasks'] + run.get('bad_tasks', [])) | length) == 0 %}
  <tr id="no-tasks">
    <td colspan=20>No tasks running</td>
  </tr>
{% endif %}
