# Gantt chart for the 22-week plan. Run: python 11_gantt.py   (needs: pip install matplotlib)
from datetime import date, timedelta
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

START = date(2026, 11, 9)          # change this to your real start date (a Monday)
tasks = [  # (name, start week, end week, colour)
    ('Proposal and literature review', 1, 3, '#7F7F7F'),
    ('Requirements analysis', 3, 5, '#7F7F7F'),
    ('Dataset creation and labelling', 4, 9, '#2F5597'),
    ('NLP model development', 8, 13, '#2F5597'),
    ('Dashboard design and development', 10, 17, '#2E8B7A'),
    ('Integration, routing and escalation', 15, 18, '#2E8B7A'),
    ('Testing and evaluation', 17, 20, '#C2410C'),
    ('Final report and documentation', 18, 22, '#B45309'),
]
fig, ax = plt.subplots(figsize=(10, 4.6))
for i, (name, s, e, c) in enumerate(tasks):
    a = START + timedelta(weeks=s - 1); b = START + timedelta(weeks=e)
    y = len(tasks) - 1 - i
    ax.barh(y, (b - a).days, left=mdates.date2num(a), color=c, edgecolor='black', height=0.55)
    ax.text(mdates.date2num(b) + 2, y, f'W{s}-W{e}', va='center', fontsize=8)
ax.set_yticks(range(len(tasks)))
ax.set_yticklabels([t[0] for t in tasks][::-1], fontsize=10)
ax.xaxis_date()
ax.xaxis.set_major_locator(mdates.WeekdayLocator(byweekday=mdates.MO, interval=2))
ax.xaxis.set_major_formatter(mdates.DateFormatter('%d %b'))
plt.setp(ax.get_xticklabels(), rotation=45, ha='right', fontsize=8)
ax.set_xlim(mdates.date2num(START), mdates.date2num(START + timedelta(weeks=24)))
ax.grid(axis='x', ls=':', alpha=0.6); ax.set_axisbelow(True)
for s in ('top', 'right'): ax.spines[s].set_visible(False)
ax.set_title('Project Gantt chart (22 weeks, start ' + START.strftime('%d %b %Y') + ')', fontsize=11)
fig.tight_layout(); fig.savefig('11_gantt.png', dpi=200)
