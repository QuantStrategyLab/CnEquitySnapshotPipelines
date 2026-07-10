from pathlib import Path


def test_publish_market_history_workflow_is_real_and_fail_closed() -> None:
    workflow = (
        Path(__file__).resolve().parents[1] / ".github" / "workflows" / "publish-market-history.yml"
    ).read_text(encoding="utf-8")

    assert "cneq-stage-akshare-market-history" in workflow
    assert "--source yahoo" in workflow
    assert "--start-date 20220101" in workflow
    assert "cn_etf_market_history.csv" in workflow
    assert "actions/upload-artifact@v4" in workflow
    assert "if-no-files-found: error" in workflow
    assert "github.event.repository.default_branch" in workflow
