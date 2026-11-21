def test(run, kit):
    assert kit.testing
    assert run({'values': []}, kit) == {'count': 0}
    assert run({'values': ['a', 'b']}, kit) == {'count': 2}
    assert run({}, kit) == {'count': 0}
