name: Shopping search

# Run on demand: go to the Actions tab, click "Run workflow", type what you
# want to search for, and the results get sent to your Telegram.
on:
  workflow_dispatch:
    inputs:
      query:
        description: "What to search for (e.g. men's casual shirt size M)"
        required: true
        default: "men's casual shirt"

jobs:
  search:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install requests
      - name: Run search
        env:
          SERPAPI_KEY: ${{ secrets.SERPAPI_KEY }}
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
          QUERY: ${{ inputs.query }}
        run: python shop_search.py
