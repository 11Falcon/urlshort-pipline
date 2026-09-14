name: continuous integration
on:
    push: 
        branches: 
            - main
            - test
    pull_request:

jobs:
    first-job:
        runs-on: ubuntu-latest
        timeout-minutes: 10
        
        steps:
            - uses: actions/checkout@v4
            - uses: actions/setup-python@v5
              with:
                python-version: '3.11'
            - name: cache
              uses: actions/cache@v4
              with:
                path: ~/.cache/pip
                key: ${{runner.os}}-pip-${{hashFiles("requirements.lock")}}
                restore-keys: ${{runner.os}}-pip-
            - name: install dependencies
              run: pip install -r requirements.lock