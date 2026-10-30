"""The suite must test what users install."""
import elem_rank
import lite_locator


def test_runs_against_installed_package():
    assert "site-packages" in elem_rank.__file__, elem_rank.__file__


def test_models_are_bundled():
    from elem_rank.lite import bundle_dir
    d = bundle_dir()
    assert d is not None and (d / "bi.onnx").exists() and (d / "ce.onnx").exists()


def test_public_api():
    for name in ("Locator", "Hits", "act", "aact", "observe", "aobserve", "outcome", "Snap"):
        assert hasattr(lite_locator, name), name
