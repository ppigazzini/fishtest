{% extends "base.mak" %}

{% set is_monthly = "monthly" in request.url %}
{% set monthly_suffix = " - Top Month" if is_monthly else "" %}

{% block title %}Contributors{{ monthly_suffix }} | Stockfish Testing{% endblock %}

{% block body %}
<script>
  (async () => {
    await DOMContentLoaded();
    const originalTable = document
      .getElementById("contributors_table")
      .cloneNode(true);

    const originalRows = Array.from(originalTable.querySelectorAll("tbody tr"));

    const includes = (row, inputValue) => {
      const cells = Array.from(row.querySelectorAll("td"));
      return cells.some((cell) => {
        const cellText = cell.textContent || cell.innerText;
        return cellText.toLowerCase().indexOf(inputValue) > -1;
      });
    };

    const searchInput = document.getElementById("search_contributors");
    searchInput.addEventListener("input", (e) => {
      filterTable(e.target.value, "contributors_table", originalRows, includes);
    });
  })();
</script>

<h2>Contributors{{ monthly_suffix }}</h2>

{% set counts = namespace(testers=0, developers=0, active_testers=0, cpu_hours=0, games=0, tests=0) %}
{% for user in users %}
  {% if user['last_updated'] != datetime.datetime.min.replace(tzinfo=datetime.UTC) %}
    {% set counts.testers = counts.testers + 1 %}
  {% endif %}
  {% if user['tests'] > 0 %}
    {% set counts.developers = counts.developers + 1 %}
  {% endif %}
  {% if user['games_per_hour'] > 0 %}
    {% set counts.active_testers = counts.active_testers + 1 %}
  {% endif %}
  {% set counts.cpu_hours = counts.cpu_hours + user['cpu_hours'] %}
  {% set counts.games = counts.games + user['games'] %}
  {% set counts.tests = counts.tests + user['tests'] %}
{% endfor %}

<div class="row g-3 mb-3">
  <div class="col-6 col-sm">
    <div class="card card-lg-sm text-center">
      <div class="card-header text-nowrap" title="Testers">Testers</div>
      <div class="card-body">
        <h4 class="card-title mb-0 monospace">
          {{ counts.testers }}
        </h4>
      </div>
    </div>
  </div>

  <div class="col-6 col-sm">
    <div class="card card-lg-sm text-center">
      <div class="card-header text-nowrap" title="Developers">Developers</div>
      <div class="card-body">
        <h4 class="card-title mb-0 monospace">
          {{ counts.developers }}
        </h4>
      </div>
    </div>
  </div>

  <div class="col-6 col-sm">
    <div class="card card-lg-sm text-center">
      <div class="card-header text-nowrap" title="Active testers">Active testers</div>
      <div class="card-body">
        <h4 class="card-title mb-0 monospace">
          {{ counts.active_testers }}
        </h4>
      </div>
    </div>
  </div>

  <div class="col-6 col-sm">
    <div class="card card-lg-sm text-center">
      <div class="card-header text-nowrap" title="CPU years">CPU years</div>
      <div class="card-body">
        <h4 class="card-title mb-0 monospace">
          {{ "{0:.2f}".format(counts.cpu_hours / (24 * 365)) }}
        </h4>
      </div>
    </div>
  </div>

  <div class="col-6 col-sm">
    <div class="card card-lg-sm text-center">
      <div class="card-header text-nowrap" title="Games played">Games played</div>
      <div class="card-body">
        <h4 class="card-title mb-0 monospace">
          {{ counts.games }}
        </h4>
      </div>
    </div>
  </div>

  <div class="col-6 col-sm">
    <div class="card card-lg-sm text-center">
      <div class="card-header text-nowrap" title="Tests submitted">Tests submitted</div>
      <div class="card-body">
        <h4 class="card-title mb-0 monospace">
          {{ counts.tests }}
        </h4>
      </div>
    </div>
  </div>
</div>

<div class="row g-3 mb-1">
  <div class="col-12 col-md-auto">
    <label class="form-label">Search</label>
    <input
      id="search_contributors"
      class="form-control"
      placeholder="Search some text"
      type="text"
    >
  </div>
</div>

<div class="table-responsive-lg">
  <table id="contributors_table" class="table table-striped table-sm">
    <thead class="sticky-top">
      <tr>
        <th></th>
        <th>Username</th>
        <th class="text-end">Last active</th>
        <th class="text-end">Games/Hour</th>
        <th class="text-end">CPU Hours</th>
        <th class="text-end">Games played</th>
        <th class="text-end">Tests submitted</th>
        <th>Tests repository</th>
      </tr>
    </thead>
    <tbody>
      {% for user in users %}
        {% set username = user['username'] %}
        {% set last_updated = user['last_updated'] %}
        <tr>
          <td class="rank">{{ loop.index }}</td>
          <td>
          {% if approver %}
            <a href="/user/{{ username }}">{{ username }}</a>
          {% else %}
            {{ username }}
          {% endif %}
          </td>
          <td data-sort-value="{{ -last_updated.timestamp() }}" class="text-end">{{ format_time_ago(last_updated) }}</td>
          <td class="text-end">{{ user['games_per_hour'] | int }}</td>
          <td class="text-end">{{ user['cpu_hours'] | int }}</td>
          <td class="text-end">{{ user['games'] | int }}</td>
          <td class="text-end">
            <a href="/tests/user/{{ urllib.quote(username) }}">{{ user['tests'] }}</a>
          </td>
          <td class="user-repo">
            <a href="{{ user['tests_repo'] }}" target="_blank" rel="noopener">{{ user['tests_repo'] }}</a>
          </td>
        </tr>
      {% else %}
        <tr>
          <td colspan=20>No users exist</td>
        </tr>
      {% endfor %}
    </tbody>
  </table>
</div>

{% endblock %}
