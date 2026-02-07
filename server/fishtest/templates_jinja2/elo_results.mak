{% if elo.is_sprt and elo.live_elo_url %}
  <a href="{{ elo.live_elo_url }}" style="color: inherit;">
{% endif %}
{% if elo.show_gauge %}
  <div id="{{ elo.chart_div_id }}" style="width:90px;float:left;"></div>
  {% if elo.is_sprt %}
    <div style="margin-left:90px;padding: 30px 0;">
  {% else %}
    <div style="margin-left:90px;">
  {% endif %}
{% endif %}
<pre {{ elo.pre_attrs | safe }}>
  {% for value in elo.info_lines %}
    {{ value }}
    {% if not loop.last %} <br> {% endif %}
  {% endfor %}
  {% if elo.nelo_summary_html %}
    <br>
    {{ elo.nelo_summary_html | safe }}
  {% endif %}
</pre>
{% if elo.show_gauge %}
  </div>
{% endif %}
{% if elo.is_sprt and elo.live_elo_url %}
  </a>
{% endif %}
