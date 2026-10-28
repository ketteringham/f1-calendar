# Formula 1 calendar

A self-updating Formula 1 calendar: Grands Prix, Sprints and pre-season testing only.
No alerts, shown as Free, and every event in the circuit's own time zone.

**Subscribe:** https://ketteringham.github.io/f1-calendar/f1.ics
(on an iPhone or Mac, open https://ketteringham.github.io/f1-calendar/ and tap *Subscribe*)

## How it works

A GitHub Action runs `build.py` once a day, and every hour from Thursday to Sunday so
race-weekend changes show up quickly. It reads the official schedule and writes:

| File | What it's for |
| --- | --- |
| `f1.ics` | The calendar, for Apple Calendar and Home Assistant |
| `f1.json` | Rich details for the Home Assistant pop-up: flags, round progress, last year's podium with headshots, standings, title watch, recent changes |

Cancelled rounds drop out on their own and the remaining rounds are renumbered, because that's
what the official schedule does. If a start time or venue changes, the event updates in place
(same event, no duplicates) and a note about the change shows for three weeks.

If the schedule can't be fetched, or suddenly looks badly wrong, the build stops and the last
good calendar stays live.

## The one file you might edit

`testing.json` holds pre-season testing days. F1 announces them months before the timing data
services list them, so they're typed in by hand until then (and marked provisional). Once the
data services have the real days, those are used instead.

## Data

* [Jolpica F1 API](https://github.com/jolpica/jolpica-f1): schedule, results, standings
* [OpenF1](https://openf1.org): driver headshots, team colours, testing sessions
* [flagcdn](https://flagcdn.com): flag images (linked, not copied)
