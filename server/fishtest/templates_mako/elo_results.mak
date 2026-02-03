<%page args="run, show_gauge=False"/>

<%!
  from fishtest.http import template_helpers as helpers
  from fishtest.util import format_results
%>
<%
    results_info = format_results(run)
%>
<%def name="list_info(run)">
  <%
    info = results_info['info']
    l = len(info)
    elo_ptnml_run = helpers.is_elo_pentanomial_run(run)
    nelo_summary = helpers.nelo_pentanomial_summary(run)
  %>
  % for i in range(l):
    ${info[i].replace("ELO", "Elo") if elo_ptnml_run and i == 0 else info[i]}
    % if i < l-1:
      <br>
    % endif
  % endfor
  % if nelo_summary:
    <br>
    ${nelo_summary|n}
  % endif
</%def>

% if 'sprt' in run['args'] and 'Pending' not in results_info['info'][0]:
  <a href="/tests/live_elo/${str(run['_id'])}" style="color: inherit;">
% endif
% if show_gauge:
  <div id="chart_div_${str(run['_id'])}" style="width:90px;float:left;"></div>
  % if 'sprt' in run['args'] and 'Pending' not in results_info['info'][0]:
    <div style="margin-left:90px;padding: 30px 0;">
  % else:
    <div style="margin-left:90px;">
  % endif
% endif
<pre ${helpers.results_pre_attrs(results_info, run)|n}>
  ${list_info(run)}
</pre>
% if show_gauge:
  </div>
% endif
% if 'sprt' in run['args'] and 'Pending' not in results_info['info'][0]:
  </a>
% endif
