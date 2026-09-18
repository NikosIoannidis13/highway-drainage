from http.client import HTTPConnection
from urllib.parse import urlsplit

from highway_drainage.presentation.satellite_view import MapPageServer


def test_map_page_serves_only_local_template_and_escapes_key() -> None:
    server = MapPageServer('dummy-key"</script>', port=0)
    address = urlsplit(server.url)
    assert address.hostname is not None
    connection = HTTPConnection(address.hostname, address.port)
    try:
        connection.request("GET", address.path)
        response = connection.getresponse()
        page = response.read().decode("utf-8")
        assert response.status == 200
        assert response.getheader("Cache-Control") == "no-store"
        assert 'dummy-key"</script>' not in page
        assert "dummy-key%22%3C%2Fscript%3E" in page
        assert "https://maps.googleapis.com/maps/api/js" in page
        assert "mapTypeId:'satellite'" in page
        connection.request("GET", "/../../pyproject.toml")
        response = connection.getresponse()
        assert response.status == 404
        response.read()
    finally:
        connection.close()
        server.close()
