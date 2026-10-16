"""Keyword / discovery search: find new social profiles by keyword, filter
them on what each platform can really measure, then deep-scrape the ones that
pass exactly like a URL search.

  capabilities.py  what each platform can measure, and which stage/actor does it
  adapters/        the only code that knows Apify actor input/output field names
  filters.py       pure filter validation + evaluation
  cache.py         short-lived profile cache shared by every run
  pipeline.py      the staged, resumable run (background thread / queue worker)
  service.py       admission, estimates, re-apply and continue (app + public API)
"""
