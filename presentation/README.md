# Revised EV charging presentation

## Deliverables
- **Grid-Aware Smart EV Charging - Revised.pptx**: editable presentation; 12 main slides and 3 backup slides, with speaker notes.
- **Grid-Aware Smart EV Charging - Revised.pdf**: matching PDF handout, rendered from the same layout definitions (not a PowerPoint export).
- **Speaker notes.md**: notes in a readable text format.

The original presentation on `main` was not modified. Suggested presentation length is 10–12 minutes, excluding backup material.

## Editorial changes
- Replaced promotional wording with plain technical explanations.
- Added related work, explicit decision variables and constraints, and a dedicated implementation slide.
- Corrected the transformer rating to 150 kVA.
- Used Round Robin as the cheapest heuristic in the reported S2 table, rather than calling EDF the best heuristic.
- Identified BILL-LP as an SOCP with a linear/piecewise-linear objective.
- Reported energy delivery alongside cost; qualified the PV-controller comparison.
- Distinguished a convex planning optimum from realized online performance and the offline benchmark.
- Corrected the historical prediction window to four weeks, rather than 28 weekdays.
- Limited the real-time timing claim to online solves, not the joint-month offline solves.
- Removed the unqualified 3% optimality claim and simplified the main narrative to S2–S4; exploratory S1 robustness is mentioned in notes rather than presented as a main result.
- Included simulation limitations and references. No experiment results were regenerated or independently reproduced.

## Sources
The supplied `main.pdf` (14 pages, including appendices), and the original presentation at:
https://github.com/milan-v-raj/OPT-techniques/blob/main/Grid-Aware%20Smart%20EV%20Charging.pptx

S3 details not tabulated in the PDF body were transcribed from the original presentation's full-results slide and identified in speaker notes. The current checkout did not contain the experiment implementation described in the report.

## Rebuild
Install `requirements.txt`, ensure DejaVu Sans regular and bold fonts exist under `/usr/share/fonts/truetype/dejavu/`, and run:

```sh
python presentation/build_deck.py
```

The presentation uses DejaVu Sans. Install that font on the presenting computer for the closest layout match; the PDF embeds fonts. All text, tables and the simple bill-composition chart remain editable PowerPoint shapes/text. PDF pages were visually reviewed; PPTX notes and shape bounds were checked programmatically. PowerPoint itself was not available for rendering validation.
