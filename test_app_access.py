from streamlit.testing.v1 import AppTest


def test_anonymous_user_sees_only_login_gate():
    app = AppTest.from_file("app.py")

    app.run(timeout=30)

    assert not app.exception
    assert [header.value for header in app.header] == ["Anmeldung erforderlich"]
    assert len(app.get("file_uploader")) == 0
    assert len(app.get("dataframe")) == 0
