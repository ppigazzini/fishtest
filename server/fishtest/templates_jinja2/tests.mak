{% extends "base.mak" %}

{% block title %}Stockfish Testing Queue | Stockfish Testing{% endblock %}

{% block body %}
{% set machines_button_label = "Hide" if machines_shown else "Show" %}
{% set machines_shown_js = "true" if machines_shown else "false" %}
<link
  rel="stylesheet"
  href="{{ static_url('fishtest:static/css/flags.css') }}"
>

<h2>Stockfish Testing Queue</h2>

{% if page_idx == 0 %}
  <div class="mw-xxl">
    <div class="row g-3 mb-3">
      <div class="col-6 col-sm">
        <div class="card card-lg-sm text-center">
          <div class="card-header text-nowrap" title="Cores">Cores</div>
          <div class="card-body">
            <h4 class="card-title mb-0 monospace">{{ cores }}</h4>
          </div>
        </div>
      </div>
      <div class="col-6 col-sm">
        <div class="card card-lg-sm text-center">
          <div class="card-header text-nowrap" title="Nodes per second">Nodes / sec</div>
          <div class="card-body">
            <h4 class="card-title mb-0 monospace">{{ nps_m }}</h4>
          </div>
        </div>
      </div>
      <div class="col-6 col-sm">
        <div class="card card-lg-sm text-center">
          <div class="card-header text-nowrap" title="Games per minute">Games / min</div>
          <div class="card-body">
            <h4 class="card-title mb-0 monospace">{{ games_per_minute }}</h4>
          </div>
        </div>
      </div>
      <div class="col-6 col-sm">
        <div class="card card-lg-sm text-center">
          <div class="card-header text-nowrap" title="Time remaining">Time remaining</div>
          <div class="card-body">
            <h4 class="card-title mb-0 monospace">{{ pending_hours }}h</h4>
          </div>
        </div>
      </div>
    </div>
  </div>

  <script>
    let fetchedMachinesBefore = false;
    let machinesSkeleton = null;
    let machinesBody = null;
    async function handleRenderMachines(){
        await DOMContentLoaded();
        machinesBody = document.getElementById("machines");
        if (!machinesSkeleton) {
          machinesSkeleton = document.querySelector("#machines .ssc-wrapper").cloneNode(true);
        }
        const machinesButton = document.getElementById("machines-button");
        machinesButton?.addEventListener("click", async () => {
          await toggleMachines();
        })
        if ({{ machines_shown_js }})
          await renderMachines();
      }

    async function renderMachines() {
      await DOMContentLoaded();
      if (fetchedMachinesBefore) {
        return Promise.resolve();
      }
      try {
        if (document.querySelector("#machines .retry")) {
          machinesBody.replaceChildren(machinesSkeleton);
        }
        const html = await fetchText("{{ urls.tests_machines }}");
        machinesBody.replaceChildren();
        machinesBody.insertAdjacentHTML("beforeend", html);
        const machinesTbody = document.querySelector("#machines tbody");
        let newMachinesCount = machinesTbody?.childElementCount;

        if (newMachinesCount === 1) {
          const noMachines = document.getElementById("no-machines");
          if (noMachines) newMachinesCount = 0;
        }

        const countSpan = document.getElementById("workers-count");
        countSpan.textContent = `Workers - ${newMachinesCount} machines`;
        fetchedMachinesBefore = true;
      } catch (error) {
        console.log("Request failed: " + error);
        machinesBody.replaceChildren();
        createRetryMessage(machinesBody, renderMachines);
      }
    }

    async function toggleMachines() {
      const button = document.getElementById("machines-button");
      const active = button.textContent.trim() === "Hide";
      if (active) {
        button.textContent = "Show";
      }
      else {
        button.textContent = "Hide";
        await renderMachines();
      }

      document.cookie =
        "machines_state" + "=" + button.textContent.trim() + "; max-age={{ 60 * 60 }}; SameSite=Lax";
    }

    handleRenderMachines();
  </script>

  <h4>
    <a id="machines-button" class="btn btn-sm btn-light border"
      data-bs-toggle="collapse" href="#machines" role="button" aria-expanded="false"
      aria-controls="machines">
      {{ machines_button_label }}
    </a>
    <span id="workers-count">
      Workers - {{ machines_count }} machines
    </span>
  </h4>
  {% set height = (machines_count * 37) | string ~ "px" %}
  {% set min_height = "37px" %}
  {% set max_height = "34.7vh" %}
  <section id="machines"
      class="overflow-auto {{ 'collapse show' if machines_shown else 'collapse' }}">
      <div class="ssc-card ssc-wrapper">
        <div class="ssc-head-line"></div>
        <div
          class="ssc-square"
          style="height: clamp({{ min_height }}, {{ height }}, {{ max_height }});">
          </div>
      </div>
  </div>
{% endif %}

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
