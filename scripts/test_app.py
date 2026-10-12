import pytest
from app.app import app as flask_app 

@pytest.fixture
def client():
    flask_app.config.update({"TESTING": True})
    with flask_app.test_client() as client:
        yield client

# def test_invalid_route_fail(client):
#     response = client.get("/wrong-path")
#     assert response.status_code == 200

def test_hello_route(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"hello Prodpai Cloud Deploy using git action" in response.data