This is an example `context_path` file (see the "Adding a database" section of the README).
Replace this entire file with real, *verified* facts about your own database -- never
guessed, never copied from a similar-looking app. A wrong "fact" here is worse than no
context at all, since the agent is told to trust it.

Keep it dense: table names, real column names, FK targets, and observed enum-like values
are far more useful than long prose or example queries. A few lines of format to copy:

- `<table_name>`: <col1>, <col2>, <col3>, ... -- one line per table, just the column names
  that actually exist (check with get_table_schema first).
- Call out anything a model might plausibly guess wrong, e.g. "There is no table called
  `orders` -- the real table is `order_table`" or "There is no `price` column -- use
  `selling_price`."
- Note observed enum-like values only if you've actually seen them (e.g. via
  get_sample_rows), phrased as "confirm others before assuming they exist."
- One line on the main relationships/foreign keys, if not obvious from table names.
- Mention if a business-metrics registry exists for this database (metrics_path) so the
  model knows to check `list_business_metrics` before writing raw SQL for a total/count.
