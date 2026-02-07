{% for task in tasks %}
  <tr class="{{ task.row_class }}" id="task{{ task.task_id }}">
    <td>
      {% if task.pgn_url %}
        <a href="{{ task.pgn_url }}">{{ task.task_id }}</a>
      {% else %}
        {{ task.task_id }}
      {% endif %}
    </td>
    <td>
      {% if task.worker_url %}
        <a href="{{ task.worker_url }}">{{ task.worker_label }}</a>
      {% else %}
        {{ task.worker_label }}
      {% endif %}
    </td>
    <td>{{ task.info_label }}</td>
    <td>{{ task.last_updated_label }}</td>
    <td>{{ task.played_label }}</td>
    {% for cell in task.results_cells %}
      <td>{{ cell }}</td>
    {% endfor %}
    <td>{{ task.crashes }}</td>
    <td>{{ task.time_losses }}</td>
    {% if show_residual %}
      <td style="background-color:{{ task.residual_bg }}">
        {{ task.residual_label }}
      </td>
    {% endif %}
  </tr>
{% endfor %}

{% if tasks | length == 0 %}
  <tr id="no-tasks">
    <td colspan=20>No tasks running</td>
  </tr>
{% endif %}
