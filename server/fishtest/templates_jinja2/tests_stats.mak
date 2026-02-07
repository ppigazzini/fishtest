{% extends "base.mak" %}

{% block title %}Stockfish Testing Framework{% endblock %}

{% block body %}
{% set z975 = fishtest.stats.stat_util.Phi_inv(0.975) %}
{% set nelo_divided_by_nt = fishtest.stats.LLRcalc.nelo_divided_by_nt %}

{% set has_sprt = "sprt" in run["args"] %}
{% set has_pentanomial = "pentanomial" in run["results"] %}
{% set has_spsa = "spsa" in run["args"] %}

{% set results3 = [run["results"]["losses"], run["results"]["draws"], run["results"]["wins"]] %}
{% set results3_ = fishtest.stats.LLRcalc.regularize(results3) %}
{% set draw_ratio = results3_[1] / (results3_ | sum) %}
{% set N3, pdf3 = fishtest.stats.LLRcalc.results_to_pdf(results3) %}
{% set games3 = N3 %}
{% set avg3, var3, skewness3, exkurt3 = fishtest.stats.LLRcalc.stats_ex(pdf3) %}
{% set stdev3 = var3 ** 0.5 %}
{% set games = games3 %}
{% set sigma = stdev3 %}
{% set pdf3_s = pdf_to_string(pdf3) %}
{% set avg3_l = avg3 - z975 * (var3 / N3) ** 0.5 %}
{% set avg3_u = avg3 + z975 * (var3 / N3) ** 0.5 %}
{% set var3_l = var3 * (1 - z975 * ((exkurt3 + 2) / N3) ** 0.5) %}
{% set var3_u = var3 * (1 + z975 * ((exkurt3 + 2) / N3) ** 0.5) %}
{% set stdev3_l = var3_l ** 0.5 if var3_l >= 0 else 0.0 %}
{% set stdev3_u = var3_u ** 0.5 %}
{% set t3_var = t_conf(avg3, var3, skewness3, exkurt3) %}
{% set t3 = t3_var[0] %}
{% set var_t3 = t3_var[1] %}
{% set t3_l = t3 - z975 * (var_t3 / N3) ** 0.5 %}
{% set t3_u = t3 + z975 * (var_t3 / N3) ** 0.5 %}
{% set nt3 = t3 %}
{% set nt3_l = t3_l %}
{% set nt3_u = t3_u %}
{% set nelo3 = nelo_divided_by_nt * nt3 %}
{% set nelo3_u = nelo_divided_by_nt * nt3_u %}
{% set nelo3_l = nelo_divided_by_nt * nt3_l %}

{% if has_pentanomial %}
  {% set results5 = run["results"]["pentanomial"] %}
  {% set results5_ = fishtest.stats.LLRcalc.regularize(results5) %}
  {% set pentanomial_draw_ratio = results5_[2] / (results5_ | sum) %}
  {% set N5, pdf5 = fishtest.stats.LLRcalc.results_to_pdf(results5) %}
  {% set games5 = 2 * N5 %}
  {% set avg5, var5, skewness5, exkurt5 = fishtest.stats.LLRcalc.stats_ex(pdf5) %}
  {% set var5_per_game = 2 * var5 %}
  {% set stdev5_per_game = var5_per_game ** 0.5 %}
  {% set games = games5 %}
  {% set sigma = stdev5_per_game %}
  {% set pdf5_s = pdf_to_string(pdf5) %}
  {% set avg5_l = avg5 - z975 * (var5 / N5) ** 0.5 %}
  {% set avg5_u = avg5 + z975 * (var5 / N5) ** 0.5 %}
  {% set var5_per_game_l = var5_per_game * (1 - z975 * ((exkurt5 + 2) / N5) ** 0.5) %}
  {% set var5_per_game_u = var5_per_game * (1 + z975 * ((exkurt5 + 2) / N5) ** 0.5) %}
  {% set stdev5_per_game_l = var5_per_game_l ** 0.5 if var5_per_game_l >= 0 else 0.0 %}
  {% set stdev5_per_game_u = var5_per_game_u ** 0.5 %}
  {% set t5_var = t_conf(avg5, var5, skewness5, exkurt5) %}
  {% set t5 = t5_var[0] %}
  {% set var_t5 = t5_var[1] %}
  {% set t5_l = t5 - z975 * (var_t5 / N5) ** 0.5 %}
  {% set t5_u = t5 + z975 * (var_t5 / N5) ** 0.5 %}
  {% set sqrt2 = 2 ** 0.5 %}
  {% set nt5 = t5 / sqrt2 %}
  {% set nt5_l = t5_l / sqrt2 %}
  {% set nt5_u = t5_u / sqrt2 %}
  {% set nelo5 = nelo_divided_by_nt * nt5 %}
  {% set nelo5_u = nelo_divided_by_nt * nt5_u %}
  {% set nelo5_l = nelo_divided_by_nt * nt5_l %}
  {% set results5_DD_prob = draw_ratio - (results5_[1] + results5_[3]) / (2 * N5) %}
  {% set results5_WL_prob = results5_[2] / N5 - results5_DD_prob %}
  {% set R3_ = copy.deepcopy(run["results"]) %}
  {% do R3_.pop("pentanomial", None) %}
  {% set ratio = var5_per_game / var3 %}
  {% set var_diff = var3 - var5_per_game %}
  {% set RMS_bias = var_diff ** 0.5 if var_diff >= 0 else 0 %}
  {% set RMS_bias_elo = fishtest.stats.stat_util.elo(0.5 + RMS_bias) %}
{% endif %}

{% set drawelo = fishtest.stats.stat_util.draw_elo_calc(results3_) %}

{% if has_sprt %}
  {% set elo_model = run["args"]["sprt"].get("elo_model", "BayesElo") %}
  {% set alpha = run["args"]["sprt"]["alpha"] %}
  {% set beta = run["args"]["sprt"]["beta"] %}
  {% set elo0 = run["args"]["sprt"]["elo0"] %}
  {% set elo1 = run["args"]["sprt"]["elo1"] %}
  {% set batch_size_units = run["args"]["sprt"].get("batch_size", 1) %}
  {% set batch_size_games = 2 * batch_size_units if has_pentanomial else 1 %}
  {% set o = run["args"]["sprt"].get("overshoot", None) %}

  {% set belo0 = None %}
  {% set belo1 = None %}
  {% if elo_model == "BayesElo" %}
    {% set belo0 = elo0 %}
    {% set belo1 = elo1 %}
    {% set elo0_ = fishtest.stats.stat_util.bayeselo_to_elo(belo0, drawelo) %}
    {% set elo1_ = fishtest.stats.stat_util.bayeselo_to_elo(belo1, drawelo) %}
    {% set elo_model_ = "logistic" %}
  {% else %}
    {% set elo0_ = elo0 %}
    {% set elo1_ = elo1 %}
    {% set elo_model_ = elo_model %}
  {% endif %}

  {% if elo_model_ == "logistic" %}
    {% set lelo0 = elo0_ %}
    {% set lelo1 = elo1_ %}
    {% set lelo03 = lelo0 %}
    {% set lelo13 = lelo1 %}
    {% set score0 = fishtest.stats.stat_util.L(lelo0) %}
    {% set score1 = fishtest.stats.stat_util.L(lelo1) %}
    {% set score03 = score0 %}
    {% set score13 = score1 %}
    {% set nelo0 = nelo_divided_by_nt * (score0 - 0.5) / sigma %}
    {% set nelo1 = nelo_divided_by_nt * (score1 - 0.5) / sigma %}
    {% set nelo03 = nelo_divided_by_nt * (score03 - 0.5) / stdev3 %}
    {% set nelo13 = nelo_divided_by_nt * (score13 - 0.5) / stdev3 %}
  {% else %}
    {% set nelo0 = elo0_ %}
    {% set nelo1 = elo1_ %}
    {% set nelo03 = nelo0 %}
    {% set nelo13 = nelo1 %}
    {% set score0 = nelo0 / nelo_divided_by_nt * sigma + 0.5 %}
    {% set score1 = nelo1 / nelo_divided_by_nt * sigma + 0.5 %}
    {% set score03 = nelo03 / nelo_divided_by_nt * stdev3 + 0.5 %}
    {% set score13 = nelo13 / nelo_divided_by_nt * stdev3 + 0.5 %}
    {% set lelo0 = fishtest.stats.stat_util.elo(score0) %}
    {% set lelo1 = fishtest.stats.stat_util.elo(score1) %}
    {% set lelo03 = fishtest.stats.stat_util.elo(score03) %}
    {% set lelo13 = fishtest.stats.stat_util.elo(score13) %}
  {% endif %}

  {% if belo0 is none %}
    {% set belo0 = fishtest.stats.stat_util.elo_to_bayeselo(lelo03, draw_ratio)[0] %}
    {% set belo1 = fishtest.stats.stat_util.elo_to_bayeselo(lelo13, draw_ratio)[0] %}
  {% endif %}

  {% set llrjumps3_raw = fishtest.stats.LLRcalc.LLRjumps(pdf3, score0, score1) %}
  {% set llrjumps3_vals = [] %}
  {% for item in llrjumps3_raw %}
    {% set _ = llrjumps3_vals.append(item[0]) %}
  {% endfor %}
  {% set LLRjumps3 = list_to_string(llrjumps3_vals) %}

  {% set sp = fishtest.stats.sprt.sprt(alpha=alpha, beta=beta, elo0=lelo0, elo1=lelo1) %}
  {% do sp.set_state(results3_) %}
  {% set a3 = sp.analytics() %}
  {% set LLR3_l = a3["a"] %}
  {% set LLR3_u = a3["b"] %}
  {% if elo_model_ == "logistic" %}
    {% set LLR3 = fishtest.stats.LLRcalc.LLR_logistic(lelo03, lelo13, results3_) %}
  {% else %}
    {% set LLR3 = fishtest.stats.LLRcalc.LLR_normalized(nelo03, nelo13, results3_) %}
  {% endif %}

  {% set elo3_l = a3["ci"][0] %}
  {% set elo3_u = a3["ci"][1] %}
  {% set elo3 = a3["elo"] %}
  {% set LOS3 = a3["LOS"] %}
  {% set LLR3_exact = N3 * fishtest.stats.LLRcalc.LLR(pdf3, score03, score13) %}
  {% set LLR3_alt = N3 * fishtest.stats.LLRcalc.LLR_alt(pdf3, score03, score13) %}
  {% set LLR3_alt2 = N3 * fishtest.stats.LLRcalc.LLR_alt2(pdf3, score03, score13) %}
  {% set LLR3_normalized = fishtest.stats.LLRcalc.LLR_normalized(nelo03, nelo13, results3_) %}
  {% set LLR3_normalized_alt = fishtest.stats.LLRcalc.LLR_normalized_alt(nelo03, nelo13, results3_) %}
  {% set LLR3_be = fishtest.stats.stat_util.LLRlegacy(belo0, belo1, results3_) %}

  {% if has_pentanomial %}
    {% set llrjumps5_raw = fishtest.stats.LLRcalc.LLRjumps(pdf5, score0, score1) %}
    {% set llrjumps5_vals = [] %}
    {% for item in llrjumps5_raw %}
      {% set _ = llrjumps5_vals.append(item[0]) %}
    {% endfor %}
    {% set LLRjumps5 = list_to_string(llrjumps5_vals) %}
    {% set sp = fishtest.stats.sprt.sprt(alpha=alpha, beta=beta, elo0=lelo0, elo1=lelo1) %}
    {% do sp.set_state(results5_) %}
    {% set a5 = sp.analytics() %}
    {% set LLR5_l = a5["a"] %}
    {% set LLR5_u = a5["b"] %}
    {% if elo_model_ == "logistic" %}
      {% set LLR5 = fishtest.stats.LLRcalc.LLR_logistic(lelo0, lelo1, results5_) %}
    {% else %}
      {% set LLR5 = fishtest.stats.LLRcalc.LLR_normalized(nelo0, nelo1, results5_) %}
    {% endif %}

    {% set o0 = 0 %}
    {% set o1 = 0 %}
    {% if o is not none %}
      {% set o0 = -o["sq0"] / o["m0"] / 2 if o["m0"] != 0 else 0 %}
      {% set o1 = o["sq1"] / o["m1"] / 2 if o["m1"] != 0 else 0 %}
    {% endif %}

    {% set elo5_l = a5["ci"][0] %}
    {% set elo5_u = a5["ci"][1] %}
    {% set elo5 = a5["elo"] %}
    {% set LOS5 = a5["LOS"] %}
    {% set LLR5_exact = N5 * fishtest.stats.LLRcalc.LLR(pdf5, score0, score1) %}
    {% set LLR5_alt = N5 * fishtest.stats.LLRcalc.LLR_alt(pdf5, score0, score1) %}
    {% set LLR5_alt2 = N5 * fishtest.stats.LLRcalc.LLR_alt2(pdf5, score0, score1) %}
    {% set LLR5_normalized = fishtest.stats.LLRcalc.LLR_normalized(nelo0, nelo1, results5_) %}
    {% set LLR5_normalized_alt = fishtest.stats.LLRcalc.LLR_normalized_alt(nelo0, nelo1, results5_) %}
  {% endif %}
{% else %}
  {% set elo3, elo95_3, LOS3 = fishtest.stats.stat_util.get_elo(results3_) %}
  {% set elo3_l = elo3 - elo95_3 %}
  {% set elo3_u = elo3 + elo95_3 %}
  {% if has_pentanomial %}
    {% set elo5, elo95_5, LOS5 = fishtest.stats.stat_util.get_elo(results5_) %}
    {% set elo5_l = elo5 - elo95_5 %}
    {% set elo5_u = elo5 + elo95_5 %}
  {% endif %}
{% endif %}

<script>
  document.title = "Statistics - {{ page_title }} | Stockfish Testing";
</script>

<div>
  {% if has_spsa %}
    <h2>SPSA tests do no have raw statistics: <a href="/tests/view/{{ run["_id"] }}">{{ run["_id"] }}</a></h2>
  {% else %}
    <h2>Raw Statistics for test <a href="/tests/view/{{ run["_id"] }}">{{ run["_id"] }}</a></h2>
    <em>Unless otherwise specified, all Elo quantities below are logistic.</em>
    <div class="row">
      <div class="col-12">
        <h4>Context</h4>
        <table class="statistics-table table table-striped table-sm">
          <thead></thead>
          <tbody>
            <tr><td>Base TC</td><td>{{ run["args"].get("tc", "?") }}</td></tr>
            <tr><td>Test TC</td><td>{{ run["args"].get("new_tc", run["args"].get("tc", "?")) }}</td></tr>
            <tr><td>Book</td><td>{{ run["args"].get("book", "?") }}</td></tr>
            <tr><td>Threads</td><td>{{ run["args"].get("threads", "?") }}</td></tr>
            <tr><td>Base options</td><td>{{ run["args"].get("base_options", "?") }}</td></tr>
            <tr><td>New options</td><td>{{ run["args"].get("new_options", "?") }}</td></tr>
          </tbody>
        </table>
        {% if has_sprt %}
          <h4>SPRT parameters</h4>
          <table class="table table-striped table-sm">
            <thead></thead>
            <tbody>
              <tr><td>Alpha</td><td>{{ alpha }}</td></tr>
              <tr><td>Beta</td><td>{{ beta }}</td></tr>
              <tr><td>Elo0 ({{ elo_model }})</td><td>{{ elo0 }}</td></tr>
              <tr><td>Elo1 ({{ elo_model }})</td><td>{{ elo1 }}</td></tr>
              <tr><td>Batch size (games) </td><td>{{ batch_size_games }}</td></tr>
            </tbody>
          </table>
        {% endif %}
        <h4>Draws</h4>
        <table class="table table-striped table-sm">
          <thead></thead>
          <tbody>
            <tr><td>Draw ratio</td><td>{{ "{0:.5f}".format(draw_ratio) }}</td></tr>
            {% if has_pentanomial %}
              <tr><td>Pentanomial draw ratio</td><td>{{ "{0:.5f}".format(pentanomial_draw_ratio) }}</td></tr>
            {% endif %}
            <tr><td>DrawElo (BayesElo)</td><td>{{ "{0:.2f}".format(drawelo) }}</td></tr>
          </tbody>
        </table>
        {% if has_sprt %}
          <h4>SPRT bounds</h4>
          <table class="table table-striped table-sm">
            <thead>
              <tr>
                <th></th>
                <th>Logistic</th>
                <th>Normalized</th>
                <th>BayesElo</th>
                <th>Score</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>H0</td>
                <td>{{ "{0:.3f}".format(lelo0) }}</td>
                <td>{{ "{0:.3f}".format(nelo0) }}</td>
                <td>{{ "{0:.3f}".format(belo0) }}</td>
                <td>{{ "{0:.5f}".format(score0) }}</td>
              </tr>
              <tr>
                <td>H1</td>
                <td>{{ "{0:.3f}".format(lelo1) }}</td>
                <td>{{ "{0:.3f}".format(nelo1) }}</td>
                <td>{{ "{0:.3f}".format(belo1) }}</td>
                <td>{{ "{0:.5f}".format(score1) }}</td>
              </tr>
            </tbody>
          </table>
          <em>
          Note: normalized Elo is inversely proportional to the square root of the number of games it takes on average to
          detect a given strength difference with a given level of significance. It is given by
          logistic_elo/(2*standard_deviation_per_game). In other words if the draw ratio is zero and Elo differences are small
          then normalized Elo and logistic Elo coincide.
          </em>
        {% endif %}
        {% if has_pentanomial %}
          <h4>Pentanomial statistics</h4>
          <h5>Basic statistics</h5>
          <table class="table table-striped table-sm">
            <thead></thead>
            <tbody>
              <tr><td>Elo</td><td>{{ "{0:.4f} [{1:.4f}, {2:.4f}]".format(elo5, elo5_l, elo5_u) }}</td></tr>
              <tr><td>LOS(1-p)</td><td>{{ "{0:.5f}".format(LOS5) }}</td></tr>
              {% if has_sprt %}
                <tr><td>LLR</td><td>{{ "{0:.4f} [{1:.4f}, {2:.4f}]".format(LLR5, LLR5_l, LLR5_u) }}</td></tr>
              {% endif %}
            </tbody>
          </table>
          {% if has_sprt %}
            <h5>Generalized Log Likelihood Ratio</h5>
            <table class="table table-striped table-sm">
              <thead></thead>
              <tbody>
                <tr><td>Logistic (exact)</td><td>{{ "{0:.5f}".format(LLR5_exact) }}</td></tr>
                <tr><td>Logistic (alt)</td><td>{{ "{0:.5f}".format(LLR5_alt) }}</td></tr>
                <tr><td>Logistic (alt2)</td><td>{{ "{0:.5f}".format(LLR5_alt2) }}</td></tr>
                <tr><td>Normalized (exact)</td><td>{{ "{0:.5f}".format(LLR5_normalized) }}</td></tr>
                <tr><td>Normalized (alt)</td><td>{{ "{0:.5f}".format(LLR5_normalized_alt) }}</td></tr>
              </tbody>
            </table>
            <em>
            Note: The quantities labeled alt and alt2 are various approximations for the
            exact quantities. Simulations indicate that the exact quantities perform
            better under extreme conditions.
            </em>
          {% endif %}
          <h5>Auxilliary statistics</h5>
          <table class="table table-striped table-sm">
            <thead></thead>
            <tbody>
              <tr><td>Games</td><td>{{ games5 | int }}</td></tr>
              <tr><td>Results [0-2]</td><td>{{ results5 }}</td></tr>
              <tr><td>Distribution</td><td>{{ pdf5_s }}</td></tr>
              <tr><td>(DD,WL) split</td><td>{{ "({0:.5f}, {1:.5f})".format(results5_DD_prob, results5_WL_prob) }}</td></tr>
              <tr><td>Expected value</td><td>{{ "{0:.5f}".format(avg5) }}</td></tr>
              <tr><td>Variance</td><td>{{ "{0:.5f}".format(var5) }}</td></tr>
              <tr><td>Skewness</td><td>{{ "{0:.5f}".format(skewness5) }}</td></tr>
              <tr><td>Excess kurtosis</td><td>{{ "{0:.5f}".format(exkurt5) }}</td></tr>
              {% if has_sprt %}
                <tr><td>Score</td><td>{{ "{0:.5f}".format(avg5) }}</td></tr>
              {% else %}
                <tr><td>Score</td><td>{{ "{0:.5f} [{1:.5f}, {2:.5f}]".format(avg5, avg5_l, avg5_u) }}</td></tr>
              {% endif %}
              <tr><td>Variance/game</td><td>{{ "{0:.5f} [{1:.5f}, {2:.5f}]".format(var5_per_game, var5_per_game_l, var5_per_game_u) }}</td></tr>
              <tr><td>Stdev/game</td><td>{{ "{0:.5f} [{1:.5f}, {2:.5f}]".format(stdev5_per_game, stdev5_per_game_l, stdev5_per_game_u) }}</td></tr>
              {% if has_sprt %}
                <tr><td>Normalized Elo</td><td>{{ "{0:.2f}".format(nelo5) }}</td></tr>
              {% else %}
                <tr><td>Normalized Elo</td><td>{{ "{0:.2f} [{1:.2f}, {2:.2f}]".format(nelo5, nelo5_l, nelo5_u) }}</td></tr>
              {% endif %}
              {% if has_sprt %}
                <tr><td>LLR jumps [0-2]</td><td>{{ LLRjumps5 }}</td></tr>
                <tr><td>Expected overshoot [H0,H1]</td><td>{{ "[{0:.5f}, {1:.5f}]".format(o0, o1) }}</td></tr>
              {% endif %}
            </tbody>
          </table>
        {% endif %}
        <h4>Trinomial statistics</h4>
        {% if has_pentanomial %}
          <em>
          Note: The following quantities are computed using the incorrect trinomial model and so they should
          be taken with a grain of salt. The trinomial quantities are listed because they serve as a sanity check
          for the correct pentanomial quantities and moreover it is possible to extract some genuinely
          interesting information from the comparison between the two.
          </em>
        {% endif %}
        <h5>Basic statistics</h5>
        <table class="table table-striped table-sm">
          <thead></thead>
          <tbody>
            <tr><td>Elo</td><td>{{ "{0:.4f} [{1:.4f}, {2:.4f}]".format(elo3, elo3_l, elo3_u) }}</td></tr>
            <tr><td>LOS(1-p)</td><td>{{ "{0:.5f}".format(LOS3) }}</td></tr>
            {% if has_sprt %}
              <tr><td>LLR</td><td>{{ "{0:.4f} [{1:.4f}, {2:.4f}]".format(LLR3, LLR3_l, LLR3_u) }}</td></tr>
            {% endif %}
          </tbody>
        </table>
        {% if has_sprt %}
          <h5>Generalized Log Likelihood Ratio</h5>
          <table class="table table-striped table-sm">
            <thead></thead>
            <tbody>
              <tr><td>Logistic (exact)</td><td>{{ "{0:.5f}".format(LLR3_exact) }}</td></tr>
              <tr><td>Logistic (alt)</td><td>{{ "{0:.5f}".format(LLR3_alt) }}</td></tr>
              <tr><td>Logistic (alt2)</td><td>{{ "{0:.5f}".format(LLR3_alt2) }}</td></tr>
              <tr><td>Normalized (exact)</td><td>{{ "{0:.5f}".format(LLR3_normalized) }}</td></tr>
              <tr><td>Normalized (alt)</td><td>{{ "{0:.5f}".format(LLR3_normalized_alt) }}</td></tr>
              <tr><td>BayesElo</td><td>{{ "{0:.5f}".format(LLR3_be) }}</td></tr>
            </tbody>
          </table>
          <em>
          Note: BayesElo is the LLR as computed using the BayesElo model. It is not clear how to
          generalize it to the pentanomial case.
          </em>
        {% endif %}
        <h5>Auxilliary statistics</h5>
        <table class="table table-striped table-sm">
          <thead></thead>
          <tbody>
            <tr><td>Games</td><td>{{ games3 | int }}</td></tr>
            <tr><td>Results [losses, draws, wins]</td><td>{{ results3 }}</td></tr>
            <tr><td>Distribution {loss ratio, draw ratio, win ratio}</td><td>{{ pdf3_s }}</td></tr>
            <tr><td>Expected value</td><td>{{ "{0:.5f}".format(avg3) }}</td></tr>
            <tr><td>Variance</td><td>{{ "{0:.5f}".format(var3) }}</td></tr>
            <tr><td>Skewness</td><td>{{ "{0:.5f}".format(skewness3) }}</td></tr>
            <tr><td>Excess kurtosis</td><td>{{ "{0:.5f}".format(exkurt3) }}</td></tr>
            {% if has_sprt %}
              <tr><td>Score</td><td>{{ "{0:.5f}".format(avg3) }}</td></tr>
            {% else %}
              <tr><td>Score</td><td>{{ "{0:.5f} [{1:.5f}, {2:.5f}]".format(avg3, avg3_l, avg3_u) }}</td></tr>
            {% endif %}
            <tr><td>Variance/game</td><td>{{ "{0:.5f} [{1:.5f}, {2:.5f}]".format(var3, var3_l, var3_u) }}</td></tr>
            <tr><td>Stdev/game</td><td>{{ "{0:.5f} [{1:.5f}, {2:.5f}]".format(stdev3, stdev3_l, stdev3_u) }}</td></tr>
            {% if has_sprt %}
              <tr><td>Normalized Elo</td><td>{{ "{0:.2f}".format(nelo3) }}</td></tr>
            {% else %}
              <tr><td>Normalized Elo</td><td>{{ "{0:.2f} [{1:.2f}, {2:.2f}]".format(nelo3, nelo3_l, nelo3_u) }}</td></tr>
            {% endif %}
            {% if has_sprt %}
              <tr><td>LLR jumps [loss, draw, win]</td><td>{{ LLRjumps3 }}</td></tr>
            {% endif %}
          </tbody>
        </table>
        {% if has_pentanomial %}
          <h4>Comparison</h4>
          <table class="table table-striped table-sm">
            <thead></thead>
            <tbody>
              <tr><td>Variance ratio (pentanomial/trinomial)</td><td>{{ "{0:.5f}".format(ratio) }}</td></tr>
              <tr><td>Variance difference (trinomial-pentanomial)</td><td>{{ "{0:.5f}".format(var_diff) }}</td></tr>
              <tr><td>RMS bias</td><td>{{ "{0:.5f}".format(RMS_bias) }}</td></tr>
              <tr><td>RMS bias (Elo)</td><td>{{ "{0:.3f}".format(RMS_bias_elo) }}</td></tr>
            </tbody>
          </table>
        {% endif %}
      </div>
    </div>
  {% endif %}
</div>
{% endblock %}
