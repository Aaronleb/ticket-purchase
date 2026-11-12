from unittest.mock import Mock

from damai_appium.fast_order import run_fast_order, validate_snapshot
from damai_appium.layout_cache import LayoutSnapshot


def make_snapshot():
    return LayoutSnapshot(
        (1080, 2400),
        {
            "card_3": {"x": 100, "y": 200},
            "confirm": {"x": 300, "y": 400},
            "viewer_0": {"x": 500, "y": 600},
            "viewer_1": {"x": 500, "y": 700},
            "submit": {"x": 700, "y": 2200},
        },
        ticket_count=1,
        price_index=3,
    )


def make_driver(checkbox):
    driver = Mock()
    driver.get_window_size.return_value = {"width": 1080, "height": 2400}
    driver.find_elements.return_value = [checkbox]
    return driver


def test_validate_snapshot_requires_submit_when_commit_enabled():
    driver = make_driver(Mock())

    assert validate_snapshot(driver, make_snapshot(), 1, 3, True) == (True, "")
    assert validate_snapshot(driver, None, 1, 3, True)[0] is False
    assert validate_snapshot(driver, make_snapshot(), 2, 3, True)[0] is False


def test_fast_order_selects_viewer_then_submits_once():
    checkbox = Mock()
    checkbox.get_attribute.side_effect = ["false", "true"]
    driver = make_driver(checkbox)
    shell = Mock()
    before_submit = Mock()

    result = run_fast_order(
        driver, make_snapshot(), shell, 1, True, before_submit
    )

    assert result.ok is True
    assert result.stages["票档与确定"] >= 0
    assert shell.batch_tap.call_args_list[0].args[0] == [(100, 200), (300, 400)]
    assert shell.batch_tap.call_args_list[1].args[0] == [(500, 600)]
    before_submit.assert_called_once_with()
    shell.tap.assert_called_once_with((700, 2200))


def test_fast_order_does_not_submit_when_viewer_verification_fails():
    checkbox = Mock()
    checkbox.get_attribute.return_value = "false"
    driver = make_driver(checkbox)
    shell = Mock()
    before_submit = Mock()

    result = run_fast_order(
        driver, make_snapshot(), shell, 1, True, before_submit
    )

    assert result.ok is False
    assert "复核失败" in result.reason
    before_submit.assert_not_called()
    shell.tap.assert_not_called()


def test_fast_order_does_not_submit_when_confirmation_page_is_missing(mocker):
    driver = make_driver(Mock())
    driver.find_elements.return_value = []
    shell = Mock()
    before_submit = Mock()
    mocker.patch("damai_appium.fast_order.time.sleep")

    result = run_fast_order(
        driver, make_snapshot(), shell, 1, True, before_submit
    )

    assert result.ok is False
    assert "未及时出现" in result.reason
    before_submit.assert_not_called()
    shell.tap.assert_not_called()
