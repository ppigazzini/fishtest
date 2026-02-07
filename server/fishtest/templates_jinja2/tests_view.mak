{% extends "base.mak" %}

{% block title %}{{ page_title }} | Stockfish Testing{% endblock %}

{% block body %}
{% if spsa_data %}
  <script src="https://www.gstatic.com/charts/loader.js"></script>
  <script>
    const spsaData = {{ spsa_data | tojson }};
  </script>
  <script src="{{ static_url("fishtest:static/js/spsa.js") }}"></script>
{% endif %}

<div id="enclosure">
  <h2>
    <span>{{ page_title }}</span>
    <a href="{{ diff.url }}" target="_blank" rel="noopener">diff</a>
  </h2>

  <div class="elo-results-top">
    {% with elo=run.elo_context %}
      {% include "elo_results.mak" %}
    {% endwith %}
  </div>

  <div class="row">
    <div class="col-12 col-lg-9">
      <div id="diff-section">
        <h4>
          Diff
          <span id="diff-num-comments"></span>
          <a
            href="{{ diff.url }}"
            class="btn btn-primary bg-light-primary border-0 mb-2"
            target="_blank" rel="noopener"
          >
            View on GitHub
          </a>
        </h4>
      </div>
      <div>
        <h4 style="margin-top: 9px;">Details</h4>
        <div class="table-responsive">
          <table class="table table-striped table-sm">
            <thead></thead>
            <tbody>
              {% for row in run_args_rows %}
                <tr>
                  <td>{{ row.name }}</td>
                  <td>
                    {% if row.link_url %}
                      <a href="{{ row.link_url }}" target="_blank" rel="noopener">
                        {{ row.value_html | safe }}
                      </a>
                    {% else %}
                      {{ row.value_html | safe }}
                    {% endif %}
                  </td>
                </tr>
              {% endfor %}
              <tr>
                <td>document size</td>
                <td>{{ document_size_label }}</td>
              </tr>
              <tr>
                <td>events</td>
                <td><a href="{{ urls.tests_actions }}">{{ urls.tests_actions }}</a></td>
              </tr>
              {% if not spsa_data %}
                <tr>
                  <td>raw statistics</td>
                  <td><a href="{{ urls.tests_stats }}">{{ urls.tests_stats }}</a></td>
                </tr>
              {% endif %}
              {% if run.approver_url %}
              <tr>
                <td>approver</td>
                <td>
                  <a href="{{ run.approver_url }}">{{ run.approver }}</a>
                </td>
              </tr>
              {% endif %}
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <div class="col-12 col-lg-3">
      <h4>Actions</h4>
      <div class="row g-2 mb-2">
        {% if can_modify_run %}
          {% if not run.finished %}
            <div class="col-12 col-sm">
              <form action="{{ urls.tests_stop }}" method="POST">
                <input type="hidden" name="run-id" value="{{ run_id }}">
                <button type="submit" class="btn btn-danger w-100">
                  Stop
                </button>
              </form>
            </div>

            {% if not run.approved and not same_user %}
              <div class="col-12 col-sm">
                <form action="{{ urls.tests_approve }}" method="POST">
                  <input type="hidden" name="run-id" value="{{ run_id }}">
                  <button type="submit" id="approve-btn"
                          class="btn {{ "btn-success" if warnings_html == [] else "btn-warning" }} w-100">
                    Approve
                  </button>
                </form>
              </div>
            {% endif %}
          {% else %}
            <div class="col-12 col-sm">
              <form action="{{ urls.tests_purge }}" method="POST">
                <input type="hidden" name="run-id" value="{{ run_id }}">
                <button type="submit" class="btn btn-danger w-100">Purge</button>
              </form>
            </div>
          {% endif %}
        {% endif %}

        <div class="col-12 col-sm">
          <button id="download_games" class="btn btn-primary text-nowrap w-100">
            Download games
          </button>
        </div>
        <div class="col-12 col-sm">
          <a class="btn btn-light border w-100" href="{{ urls.tests_run }}">Reschedule</a>
        </div>
      </div>
      <hr>
      {% if warnings_html %}
        <div class="alert alert-danger">
          {% for warning in warnings_html %}
            Warning: {{ warning | safe }}
            {% if not loop.last %}
              <hr style="margin: 0.4em auto;">
            {% endif %}
          {% endfor %}
        </div>
      {% endif %}

      {% if notes_html %}
        <div class="alert alert-info">
          {% for note in notes_html %}
            Note: {{ note | safe }}
            {% if not loop.last %}
              <hr style="margin: 0.4em auto;">
            {% endif %}
          {% endfor %}
        </div>
      {% endif %}

      <hr>

      {% if can_modify_run %}
        <form class="form" action="{{ urls.tests_modify }}" method="POST">
          <div class="mb-3">
            <label class="form-label" for="modify-num-games">Number of games</label>
            <input
              type="number"
              class="form-control"
              name="num-games"
              id="modify-num-games"
              min="0"
              step="1000"
              value="{{ run.num_games }}"
            >
          </div>

          <div class="mb-3">
            <label class="form-label" for="modify-priority">Priority (higher is more urgent)</label>
            <input
              type="number"
              class="form-control"
              name="priority"
              id="modify-priority"
              value="{{ run.priority }}"
            >
          </div>

          <label class="form-label" for="modify-throughput">Throughput</label>
          <div class="mb-3 input-group">
            <input
              type="number"
              class="form-control"
              name="throughput"
              id="modify-throughput"
              min="0"
              value="{{ run.throughput }}"
            >
            <span class="input-group-text">%</span>
          </div>

          {% if same_user %}
            <div class="mb-3">
              <label for="info" class="form-label">
                Info
              </label>
              <textarea
                id="modify-info"
                name="info"
                placeholder="Defaults to submitted message."
                class="form-control"
                rows="4"
                style="height: 149px;"
              ></textarea>
            </div>
          {% endif %}

          <div class="mb-3 form-check">
            <input
              type="checkbox"
              class="form-check-input"
              id="auto-purge"
              name="auto_purge" {{ "checked" if run.auto_purge else "" }}
            >
            <label class="form-check-label" for="auto-purge">Auto-purge</label>
          </div>

          <input type="hidden" name="run" value="{{ run_id }}">
          <button type="submit" class="btn btn-primary col-12 col-md-auto">Modify</button>
        </form>
      {% endif %}

      {% if chi2_rows %}
        <hr>
        <h4>Stats</h4>
        <table class="table table-striped table-sm">
          <thead></thead>
          <tbody>
            {% for row in chi2_rows %}
              <tr><td>{{ row.label }}</td><td>{{ row.value }}</td></tr>
            {% endfor %}
          </tbody>
        </table>
      {% endif %}

      <hr>

      <h4>Time</h4>
      <table class="table table-striped table-sm">
        <thead></thead>
        <tbody>
          <tr><td>start time</td><td>{{ run.start_time_label }}</td></tr>
          <tr><td>last updated</td><td>{{ run.last_updated_label }}</td></tr>
        </tbody>
      </table>

      <hr>
      {% if not run.finished %}
        <h4>Notifications</h4>
        <button
          id="follow_button_{{ run_id }}"
          class="btn btn-primary col-12 col-md-auto"
          onclick="handleFollowButton(this)"
          style="display:none; margin-top:0.2em;"></button>
        <hr style="visibility:hidden;">
      {% endif %}
    </div>
  </div>

  <h4>
      <a
        id="tasks-button" class="btn btn-sm btn-light border"
        data-bs-toggle="collapse" href="#tasks" role="button" aria-expanded="false"
        aria-controls="tasks"
      >
        {{ "Hide" if tasks_shown else "Show" }}
      </a>
    Tasks {{ tasks_total_label }}
  </h4>
  <section id="tasks"
       class="overflow-auto {{ "collapse show" if tasks_shown else "collapse" }}">
    <table class='table table-striped table-sm'>
      <thead id="tasks-head" class="sticky-top">
        <tr>
          <th>Idx</th>
          <th>Worker</th>
          <th>Info</th>
          <th>Last Updated</th>
          <th>Played</th>
          <th>Results</th>
          <th>Crashes</th>
          <th>Time</th>
          <th>Residual</th>
        </tr>
      </thead>
      <tbody id="tasks-body"></tbody>
    </table>
  </section>
</div>

<script>
  async function handleRenderTasks(){
    await DOMContentLoaded();
    const tasksButton = document.getElementById("tasks-button");
    tasksButton?.addEventListener("click", async () => {
      await toggleTasks();
    })
     if ({{ "true" if tasks_shown else "false" }})
       await renderTasks();
  }

  async function renderTasks() {
    await DOMContentLoaded();
    const tasksBody = document.getElementById("tasks-body");
    try {
      const html = await fetchText("{{ urls.tests_tasks }}?show_task={{ show_task }}");
      tasksBody.innerHTML = html;
    } catch (error) {
      console.log("Request failed: " + error);
    }
  }

  async function toggleTasks() {
    const button = document.getElementById("tasks-button");
    const active = button.textContent.trim() === "Hide";
    if (active){
      button.textContent = "Show";
    }
    else {
      await renderTasks();
      button.textContent = "Hide";
    }

    document.cookie =
      "tasks_state" + "=" + button.textContent.trim() + "; max-age={{ 60 * 60 }}; SameSite=Lax";
  }

  handleRenderTasks();
</script>
{% endblock %}
