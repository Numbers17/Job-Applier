# Job-Applier

A Python pipeline that finds new software internship and new-grad postings, filters them down to the relevant ones, tailors a resume to each job description with the Claude API, and pre-fills the application form in a real browser — then stops so you can review and submit it yourself.

**It never clicks submit.** Every application is filled for a human to check, not sent automatically.

## What it does

1. **Fetch listings** — pulls live postings from several community-maintained GitHub job boards (e.g. `vanshb03/Summer2027-Internships`, `speedyapply/2027-SWE-College-Jobs`, `zapplyjobs/*`), parsing both of the README table formats they use.
2. **Filter and rank** — keeps open SWE / backend / full-stack / PM roles and sorts preferred locations to the top.
3. **Check descriptions** — visits each posting and soft-flags language the listing table doesn't show, like grad-degree requirements.
4. **Review** — writes everything to `output/candidates.csv` so you can approve or skip roles before any applying happens.
5. **Tailor a resume** — for each approved role, Claude picks and lightly rewords bullets from your experience bank to match the job description. It references bullets by ID and a fact-check pass rejects any reworded bullet that adds claims not in the original, so the resume can't invent experience. Near-duplicate job descriptions reuse an existing tailored resume (local word-overlap matching) instead of making another API call.
6. **Autofill** — opens the application in Playwright and fills standard fields from your profile, uploads the tailored resume, and answers dropdown/radio/checkbox questions (EEOC, work authorization, referral source…) using answers you've given before. New questions are asked once and remembered.
7. **Track** — generates an Excel tracker of what you've applied to, skipped, or still need to review.

### Supported application systems

| ATS | Support |
|---|---|
| Greenhouse | Full — standard fields + custom questions |
| Lever | Full — standard fields + custom questions |
| Ashby | Full — standard fields + custom questions |
| Workday | Navigates to the account sign-in step (Workday requires an account per company) |
| Other career pages | Best-effort heuristic: matches field labels to profile fields; anything unmatched is logged for manual fill |

Each autofill run saves a JSON log of every field it attempted (value, selector, status) and a full-page screenshot to `output/logs/`.

## Tech stack

Python · Playwright · Anthropic Claude API · BeautifulSoup · Requests · openpyxl

## Setup

```bash
pip install -r requirements.txt
playwright install chromium
```

Create a `.env` file with your API key (only needed for resume tailoring):

```
ANTHROPIC_API_KEY=your-key-here
```

Copy the example files and fill in your own details. The real files are gitignored, along with resumes, output, and the browser profile.

```bash
cp profile.example.json profile.json                  # name, contact info, school, links
cp experience_bank.example.json experience_bank.json  # all your experience/project bullets
cp answers.example.json answers.json                  # remembered answers to application questions
```

## Usage

```bash
# 1. Fetch, filter, and check postings -> output/candidates.csv
python scraper/run_pipeline.py --limit 40

# 2. Review candidates.csv, then tailor resumes and autofill approved postings
python scraper/autofill.py --limit 5

#    Or spot-check a single posting
python scraper/autofill.py --url <posting-url> --company "Acme" --role "SWE Intern"

# 3. Build the application tracker -> output/application_tracker.xlsx
python scraper/build_tracker.py
```

Useful `autofill.py` flags: `--listing-type internship|new_grad`, `--standard-only` (Greenhouse/Lever/Ashby only), `--headless`.
