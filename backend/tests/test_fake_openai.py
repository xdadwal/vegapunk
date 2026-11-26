import httpx

from tests.fake_openai import FakeOpenAI, create_app, serve


def test_the_fake_serves_models_and_streams_scripted_turns():
    fake = FakeOpenAI(
        script=[
            {"text": ["Hel", "lo"]},
            {"tool_calls": [{"id": "c1", "name": "x", "arguments": {"a": 1}}]},
        ],
        required_key="secret",
    )
    with serve(create_app(fake)) as url:
        auth = {"Authorization": "Bearer secret"}
        assert httpx.get(f"{url}/v1/models", headers=auth).json()["data"][0]["id"] == "model-b"
        assert httpx.get(f"{url}/v1/models").status_code == 401
        assert (
            httpx.get(f"{url}/v1/models", headers={"Authorization": "Bearer nope"}).status_code
            == 401
        )

        body = {"model": "m", "messages": [{"role": "user", "content": "hi"}], "stream": True}
        text = httpx.post(f"{url}/v1/chat/completions", json=body, headers=auth).text
        assert text.count("data: ") >= 5 and text.rstrip().endswith("data: [DONE]")
        assert '"content": "Hel"' in text and '"finish_reason": "stop"' in text

        tools = httpx.post(f"{url}/v1/chat/completions", json=body, headers=auth).text
        assert '"name": "x"' in tools and '"finish_reason": "tool_calls"' in tools

        exhausted = httpx.post(f"{url}/v1/chat/completions", json=body, headers=auth)
        assert exhausted.status_code == 500
    assert len(fake.requests) == 3 and fake.requests[0]["body"]["model"] == "m"
    assert [r["authorization"] for r in fake.model_requests] == [
        "Bearer secret",
        None,
        "Bearer nope",
    ]


def test_a_scripted_failure_can_carry_its_own_error_message():
    fake = FakeOpenAI(
        script=[
            {"status": 400, "message": "response_format json_schema is not supported"},
            {"status": 500},
        ]
    )
    with serve(create_app(fake)) as url:
        body = {"model": "m", "messages": [{"role": "user", "content": "hi"}], "stream": True}
        first = httpx.post(f"{url}/v1/chat/completions", json=body)
        second = httpx.post(f"{url}/v1/chat/completions", json=body)
    assert first.status_code == 400
    assert first.json()["error"]["message"] == "response_format json_schema is not supported"
    assert second.json()["error"]["message"] == "scripted failure"
