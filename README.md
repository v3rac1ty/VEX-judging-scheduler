# VEX-judging-scheduler
Generates a schedule for VEX teams to judge during the inspection period.

## Quick start
1. Install dependencies:
	```bash
	python -m venv .venv
	source .venv/bin/activate
	pip install -r requirements.txt
	```
2. Run the app:
	```bash
	uvicorn app:app --reload
	```
3. Open the UI at http://127.0.0.1:8000

## Inputs
- Judge pairs, slot length, judging window length, match block minutes.
- Judging start time (local).
- Match schedule: paste the JSON array of matches or the log that contains it.