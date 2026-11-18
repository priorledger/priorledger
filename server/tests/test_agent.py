import io
import json

from priorledger import agent


def test_submit_names_itself(monkeypatch):
    seen = {}

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        seen["ua"] = req.get_header("User-agent")
        seen["url"] = req.full_url
        return Resp(json.dumps({"pid": 1}).encode())

    monkeypatch.setattr(agent.urllib.request, "urlopen", fake_urlopen)
    assert agent.submit("https://example.test/", {"x": 1}, "00") == {"pid": 1}
    assert seen["url"] == "https://example.test/api/proposals"
    assert seen["ua"].startswith("priorledger-agent/")
    assert "python" not in seen["ua"].lower()
