# Break it on purpose, and write down what you saw:
## the failure:
in the file ```tests/test_store.py``` i changed the code on line 7 from `a` to `asoufiane`
so that the test can fail.
## Get to the failing line : 
withe the command ``gh run list``
``STATUS  TITLE                           WORKFLOW  BRANCH  EVENT  ID           ELAPSED  AGE                   
X       changeing the yaml workflow     ci        test    push   33259751723  19s      less than a minute ago
✓       make it says what it does       ci        main    push   33250144221  19s      about 3 hours ago
✓       make the run say what it build  ci        main    push   33249915117  19s      about 3 hours ago
✓       version2                        ci        main    push   33248314765  17s      about 4 hours ago
X       version1                        ci        main    push   33247948002  0s       about 4 hours ago
X       first workflow                  ci        main    push   33244277825  12s      about 6 hours ago``
and 
using the command `gh run <the_id of the workflow> --log-failed i got the following output : 
```
test    Run pytest -m "not slow"        2026-08-29T15:15:42.7881036Z ##[group]Run pytest -m "not slow"
test    Run pytest -m "not slow"        2026-08-29T15:15:42.7881383Z ^[[36;1mpytest -m "not slow"^[[0m
test    Run pytest -m "not slow"        2026-08-29T15:15:42.7924337Z shell: /usr/bin/bash -e {0}
test    Run pytest -m "not slow"        2026-08-29T15:15:42.7924599Z env:
test    Run pytest -m "not slow"        2026-08-29T15:15:42.7924882Z   pythonLocation: /opt/hostedtoolcache/Python/3.11.16/x64
test    Run pytest -m "not slow"        2026-08-29T15:15:42.7925329Z   PKG_CONFIG_PATH: /opt/hostedtoolcache/Python/3.11.16/x64/lib/pkgconfig
test    Run pytest -m "not slow"        2026-08-29T15:15:42.7925751Z   Python_ROOT_DIR: /opt/hostedtoolcache/Python/3.11.16/x64
test    Run pytest -m "not slow"        2026-08-29T15:15:42.7926153Z   Python2_ROOT_DIR: /opt/hostedtoolcache/Python/3.11.16/x64
test    Run pytest -m "not slow"        2026-08-29T15:15:42.7926563Z   Python3_ROOT_DIR: /opt/hostedtoolcache/Python/3.11.16/x64
test    Run pytest -m "not slow"        2026-08-29T15:15:42.7926952Z   LD_LIBRARY_PATH: /opt/hostedtoolcache/Python/3.11.16/x64/lib
test    Run pytest -m "not slow"        2026-08-29T15:15:42.7927282Z ##[endgroup]
test    Run pytest -m "not slow"        2026-08-29T15:15:44.0285954Z .....F...........                                                        [100%]
test    Run pytest -m "not slow"        2026-08-29T15:15:44.0286845Z =================================== FAILURES ===================================
test    Run pytest -m "not slow"        2026-08-29T15:15:44.0287497Z ______________________ test_encode_is_stable_and_ordered _______________________
test    Run pytest -m "not slow"        2026-08-29T15:15:44.0288050Z 
test    Run pytest -m "not slow"        2026-08-29T15:15:44.0288282Z     def test_encode_is_stable_and_ordered():
test    Run pytest -m "not slow"        2026-08-29T15:15:44.0288948Z >       assert encode(0) == "asoufiane"
test    Run pytest -m "not slow"        2026-08-29T15:15:44.0289386Z E       AssertionError: assert 'a' == 'asoufiane'
test    Run pytest -m "not slow"        2026-08-29T15:15:44.0289807Z E         
test    Run pytest -m "not slow"        2026-08-29T15:15:44.0290113Z E         - asoufiane
test    Run pytest -m "not slow"        2026-08-29T15:15:44.0290429Z E         + a
test    Run pytest -m "not slow"        2026-08-29T15:15:44.0290618Z 
test    Run pytest -m "not slow"        2026-08-29T15:15:44.0290780Z tests/test_store.py:7: AssertionError
test    Run pytest -m "not slow"        2026-08-29T15:15:44.0291257Z =============================== warnings summary ===============================
test    Run pytest -m "not slow"        2026-08-29T15:15:44.0292083Z ../../../../../opt/hostedtoolcache/Python/3.11.16/x64/lib/python3.11/site-packages/fastapi/testclient.py:1
test    Run pytest -m "not slow"        2026-08-29T15:15:44.0293748Z   /opt/hostedtoolcache/Python/3.11.16/x64/lib/python3.11/site-packages/fastapi/testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
test    Run pytest -m "not slow"        2026-08-29T15:15:44.0295238Z     from starlette.testclient import TestClient as TestClient  # noqa
test    Run pytest -m "not slow"        2026-08-29T15:15:44.0295688Z 
test    Run pytest -m "not slow"        2026-08-29T15:15:44.0296006Z -- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
test    Run pytest -m "not slow"        2026-08-29T15:15:44.0296701Z =========================== short test summary info ============================
test    Run pytest -m "not slow"        2026-08-29T15:15:44.0297671Z FAILED tests/test_store.py::test_encode_is_stable_and_ordered - AssertionError: assert 'a' == 'asoufiane'
test    Run pytest -m "not slow"        2026-08-29T15:15:44.0299088Z   
test    Run pytest -m "not slow"        2026-08-29T15:15:44.0299359Z   - asoufiane
test    Run pytest -m "not slow"        2026-08-29T15:15:44.0299649Z   + a
test    Run pytest -m "not slow"        2026-08-29T15:15:44.0300018Z 1 failed, 16 passed, 3 deselected, 1 warning in 0.40s
test    Run pytest -m "not slow"        2026-08-29T15:15:44.1004029Z ##[error]Process completed with exit code 1.
```
so the log tells exactly what is the Failure and the line exactly.

after fixing that failure the code returns to it's first state: 