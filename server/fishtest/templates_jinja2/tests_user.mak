{% extends "base.mak" %}

{% block body %}
<h2>
{% if is_approver %}
  <a href="/user/{{ username }}">{{ username }}</a> - Tests
{% else %}
  {{ username }} - Tests
{% endif %}
</h2>

<script>
  document.title = "{{ username }} | Stockfish Testing";
</script>

{% include "run_tables.mak" %}
{% endblock %}
