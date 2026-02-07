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
    {% for machine in machines %}
      <tr>
        <td>{{ machine.username }}</td>
        <td>
          {% if machine.country_code %}
            <div class="flag flag-{{ machine.country_code }}"
                 style="display: inline-block"></div>
          {% endif %}
          {{ machine.concurrency }}
        </td>
        <td><a href="{{ machine.worker_url }}">{{ machine.worker_short }}</a></td>
        <td>{{ machine.nps_m }}</td>
        <td>{{ machine.max_memory }}</td>
        <td>{{ machine.system }}</td>
        <td>{{ machine.worker_arch }}</td>
        <td>{{ machine.compiler_label }}</td>
        <td>{{ machine.python_label }}</td>
        <td>{{ machine.version_label }}</td>
        <td>
          <a href="{{ machine.run_url }}" title="{{ machine.run_label }}">{{ machine.run_label }}</a>
        </td>
        <td data-sort-value="{{ machine.last_active_sort }}">{{ machine.last_active_label }}</td>
      </tr>
    {% else %}
      <tr id="no-machines">
        <td colspan=20>No machines running</td>
      </tr>
    {% endfor %}
  </tbody>
</table>
