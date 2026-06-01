from amz_sif_crawler.api.app import build_app


def test_build_app():
    app = build_app()
    assert app is not None
