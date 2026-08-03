from pathlib import Path

import httpx

from backend.scripts import sync_official_guidance as guidance


OFFICIAL_URL = "https://www.oceanengine.com/solution/food-drink"


def test_official_url_allowlist() -> None:
    assert guidance.is_official_url(OFFICIAL_URL)
    assert guidance.is_official_url("https://life.douyin.com/example")
    assert not guidance.is_official_url("http://www.oceanengine.com/example")
    assert not guidance.is_official_url("https://oceanengine.com.example.org/example")


def test_extract_visible_candidate_rules() -> None:
    parser = guidance.VisibleTextParser()
    parser.feed(
        """
        <html><head><title>官方内容指南</title><style>短视频隐藏文字</style></head>
        <body><main>
          <h1>餐饮内容经营</h1>
          <p>通过短视频展示产品卖点，持续影响目标人群并完成到店转化。</p>
          <script>品牌人设隐藏脚本</script>
        </main></body></html>
        """
    )

    candidates = guidance.extract_candidates(parser.text, OFFICIAL_URL)

    assert parser.title == "官方内容指南"
    assert len(candidates) == 1
    assert {"value", "retention", "conversion", "production"}.issubset(
        candidates[0]["tags"]
    )
    assert "隐藏" not in candidates[0]["statement"]


def test_sync_marks_changes_and_keeps_updates_review_only(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /", request=request)
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8", "etag": "v1"},
            text=(
                "<html><head><title>食品餐饮方案</title></head><body>"
                "<p>企业号成为固定店铺入口，通过短视频内容引流塑造品牌人设，"
                "达成长期经营和到店转化。</p></body></html>"
            ),
            request=request,
        )

    with httpx.Client(
        transport=httpx.MockTransport(handler), follow_redirects=True
    ) as client:
        first = guidance.sync_guidance(client, [OFFICIAL_URL], delay_seconds=0)
        second = guidance.sync_guidance(
            client, [OFFICIAL_URL], previous=first, delay_seconds=0
        )

    assert first["sources"][0]["change_status"] == "new"
    assert second["sources"][0]["change_status"] == "unchanged"
    assert first["candidate_rules"]
    assert first["review_required"] is True
    assert first["auto_apply"] is False

    output = tmp_path / "guidance.json"
    guidance.write_snapshot(output, first)
    assert output.is_file()
    assert "candidate_rules" in output.read_text(encoding="utf-8")
