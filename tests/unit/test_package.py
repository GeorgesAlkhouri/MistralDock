def test_package_exposes_version() -> None:
    import mistraldock

    assert mistraldock.__version__ == "0.1.0"
