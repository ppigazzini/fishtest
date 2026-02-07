{% extends "base.mak" %}

{% block title %}Statistics - {{ page_title }} | Stockfish Testing{% endblock %}

{% block body %}
<div>
  {% if has_spsa %}
    <h2>SPSA tests do not have raw statistics: <a href="{{ run_url }}">{{ run_id }}</a></h2>
  {% else %}
    <h2>Raw Statistics for test <a href="{{ run_url }}">{{ run_id }}</a></h2>
    <em>Unless otherwise specified, all Elo quantities below are logistic.</em>
    <div class="row">
      <div class="col-12">
        <h4>Context</h4>
        <table class="statistics-table table table-striped table-sm">
          <thead></thead>
          <tbody>
            {% for row in context_rows %}
              <tr><td>{{ row.label }}</td><td>{{ row.value }}</td></tr>
            {% endfor %}
          </tbody>
        </table>

        {% if has_sprt %}
          <h4>SPRT parameters</h4>
          <table class="table table-striped table-sm">
            <thead></thead>
            <tbody>
              {% for row in sprt_rows %}
                <tr><td>{{ row.label }}</td><td>{{ row.value }}</td></tr>
              {% endfor %}
            </tbody>
          </table>
        {% endif %}

        <h4>Draws</h4>
        <table class="table table-striped table-sm">
          <thead></thead>
          <tbody>
            {% for row in draw_rows %}
              <tr><td>{{ row.label }}</td><td>{{ row.value }}</td></tr>
            {% endfor %}
          </tbody>
        </table>

        {% for section in pentanomial_sections %}
          <h4>{{ section.title }}</h4>
          <table class="table table-striped table-sm">
            <thead></thead>
            <tbody>
              {% for row in section.rows %}
                <tr><td>{{ row.label }}</td><td>{{ row.value }}</td></tr>
              {% endfor %}
            </tbody>
          </table>
          {% if section.note %}
            <em>{{ section.note }}</em>
          {% endif %}
        {% endfor %}

        {% for section in trinomial_sections %}
          <h4>{{ section.title }}</h4>
          <table class="table table-striped table-sm">
            <thead></thead>
            <tbody>
              {% for row in section.rows %}
                <tr><td>{{ row.label }}</td><td>{{ row.value }}</td></tr>
              {% endfor %}
            </tbody>
          </table>
          {% if section.note %}
            <em>{{ section.note }}</em>
          {% endif %}
        {% endfor %}

        {% if comparison_rows %}
          <h4>Comparison</h4>
          <table class="table table-striped table-sm">
            <thead></thead>
            <tbody>
              {% for row in comparison_rows %}
                <tr><td>{{ row.label }}</td><td>{{ row.value }}</td></tr>
              {% endfor %}
            </tbody>
          </table>
        {% endif %}
      </div>
    </div>
  {% endif %}
</div>
{% endblock %}
