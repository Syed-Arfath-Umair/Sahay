"""Minimal test runner (no pytest needed): python tests/run_tests.py"""
import inspect
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_core  # noqa: E402


class MonkeyPatch:
    def __init__(self):
        self._undo = []

    def setattr(self, obj, name, value):
        self._undo.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    def undo(self):
        for obj, name, old in reversed(self._undo):
            setattr(obj, name, old)


def main() -> int:
    tests = [(n, f) for n, f in vars(test_core).items() if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        mp = MonkeyPatch()
        try:
            fn(mp) if "monkeypatch" in inspect.signature(fn).parameters else fn()
            print(f"PASS {name}")
        except Exception:
            failed += 1
            print(f"FAIL {name}")
            traceback.print_exc()
        finally:
            mp.undo()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
