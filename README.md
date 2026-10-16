# CouponzGuru Brand Finder

Finds consumer brands that aren't onboarded yet, saves them to the `tool_search` tab, and drafts outreach.

```bash
./run.sh   # http://127.0.0.1:8800
```

## How a search works (LangGraph, `app/graph.py`)

1. **load_sheet**: reads every brand in the 6 country tabs (India, USA, UAE, Singapore, Malaysia, Philippines).
2. **prune**: deletes `tool_search` rows for brands you've since added to a country tab.
3. **discover**: runs in parallel for each country × category × source:
   - `llm`: LangChain fallback chain Groq gpt-oss-120b → Groq gpt-oss-20b → Gemini → OpenAI
   - `groq_web`: Groq live browser search (`GROQ_WEB_SEARCH=1`; uses a lot of free-tier tokens)
   - `serpapi`: Google results → LLM extracts brand names (needs `SERPAPI_API_KEY`)
4. **filter_new**: fuzzy-matches names against onboarded and existing results, then merges duplicates.
5. **enrich**: Brandfetch (domain and logo), website scrape (email, phone, LinkedIn), Apollo, BuiltWith.
6. **save**: appends rows to `tool_search` (brand_name, catagory, county, data_source, website, linkedin, email, phone, logo, summary, added_at).

A source with no key, or a key without access (for example Apollo's free plan), is skipped automatically.

## Outreach

Clicking a brand generates an AI summary, which is cached in the `summary` column. The **Send email** and **Send LinkedIn message** boxes draft a CouponzGuru collaboration message.
Email can be opened in Gmail or your mail app. Set `SMTP_USER`/`SMTP_PASSWORD` (a Gmail App Password) to enable **Send now**.
LinkedIn has no public messaging API, so the app copies the message and opens the company page for you to paste.
# CG-AI-Brandfetch-algorithm
