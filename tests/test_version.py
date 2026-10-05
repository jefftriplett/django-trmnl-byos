from django_trmnl_byos import __version__


def test_version_txt(client):
    """Plain-text version that `just check-versions` reads on live sites."""
    response = client.get("/version.txt")

    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/plain")
    assert response.content.decode() == __version__
