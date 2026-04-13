"""``agora21`` package main shim."""

import agora21.__main__ as m


def test_agora21_main_defines_main() -> None:
    assert callable(m.main)
