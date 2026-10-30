---
name: chart
description: Make a clean bar, line or pie chart PNG from a CSV.
tags: [chart, graph, plot, png, visualise, csv]
---
# chart

    python /buddy/skills/chart/chart.py DATA.csv output/chart.png --x month --y spend --y budget \
        --type line --title "Spend vs budget"

`--y` can repeat for several series (bar or line). For a pie, one `--y`.
