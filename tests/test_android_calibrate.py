from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PIL import Image

import android_calibrate


def png_bytes():
    image = Image.new("RGB", (1000, 2000), (20, 30, 40))
    for y in range(1740, 1860):
        for x in range(350, 650):
            image.putpixel((x, y), (255, 255, 255))
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def mock_capture(mocker, tmp_path):
    capture_path = tmp_path / "capture.png"
    temporary = Mock()
    temporary.__enter__ = Mock(return_value=SimpleNamespace(name=str(capture_path)))
    temporary.__exit__ = Mock(return_value=False)
    mocker.patch.object(
        android_calibrate.tempfile, "NamedTemporaryFile", return_value=temporary
    )

    def capture(*_args, **kwargs):
        kwargs["stdout"].write(png_bytes())
        return SimpleNamespace(returncode=0)

    mocker.patch.object(android_calibrate.subprocess, "run", side_effect=capture)
    mocker.patch.object(
        android_calibrate,
        "_foreground_activity",
        return_value="cn.damai/.ProjectDetailActivity",
    )


def test_positive_buy_text_saves_template(mocker, tmp_path):
    mock_capture(mocker, tmp_path)
    mocker.patch.object(
        android_calibrate,
        "_ocr_rows",
        return_value=[(500, 1800, "立即预订")],
    )
    dump = mocker.patch.object(android_calibrate, "_dump_buy_bar_center")
    templates = tmp_path / "templates"

    result = android_calibrate.calibrate_android_point("fake", template_dir=templates)

    assert result["template_path"] == str(templates / "buy.png")
    assert (templates / "buy.png").is_file()
    dump.assert_not_called()


def test_dump_fallback_does_not_save_unverified_template(mocker, tmp_path):
    mock_capture(mocker, tmp_path)
    mocker.patch.object(android_calibrate, "_ocr_rows", return_value=[])
    mocker.patch.object(
        android_calibrate, "_dump_buy_bar_center", return_value=(500, 1800)
    )
    templates = tmp_path / "templates"

    result = android_calibrate.calibrate_android_point("fake", template_dir=templates)

    assert result["point"] == "500,1800"
    assert "template_path" not in result
    assert not (templates / "buy.png").exists()


def test_saved_template_is_grayscale_button_crop(tmp_path):
    image_path = tmp_path / "screen.png"
    image_path.write_bytes(png_bytes())

    destination = android_calibrate._save_buy_template(
        str(image_path), (500, 1800), tmp_path / "templates"
    )

    template = Image.open(destination)
    assert template.mode == "L"
    assert template.size == (360, 140)


def test_dim_template_warns_about_presale_button(tmp_path):
    dark = tmp_path / "dark.png"
    Image.new("L", (100, 40), 60).save(dark)
    assert "未开售" in android_calibrate._template_warning(str(dark))

    bright = tmp_path / "bright.png"
    Image.new("L", (100, 40), 240).save(bright)
    assert android_calibrate._template_warning(str(bright)) == ""


def test_negative_text_saves_negative_template(mocker, tmp_path):
    mock_capture(mocker, tmp_path)
    mocker.patch.object(
        android_calibrate,
        "_ocr_rows",
        return_value=[(500, 1800, "已预约")],
    )
    templates = tmp_path / "templates"

    result = android_calibrate.calibrate_negative_point(
        "fake", template_dir=templates
    )

    destination = templates / "negative-yuyue.png"
    assert result["template_path"] == str(destination)
    assert result["button"] == "已预约"
    assert destination.is_file()
    template = Image.open(destination)
    assert template.mode == "L"
    assert template.size == (360, 140)


def test_negative_template_name_follows_matched_text(mocker, tmp_path):
    mock_capture(mocker, tmp_path)
    mocker.patch.object(
        android_calibrate,
        "_ocr_rows",
        return_value=[(500, 1800, "未开售")],
    )
    templates = tmp_path / "templates"

    result = android_calibrate.calibrate_negative_point(
        "fake", template_dir=templates
    )

    assert result["template_path"] == str(templates / "negative-weikaisou.png")
    assert (templates / "negative-weikaisou.png").is_file()


def test_negative_requires_presale_text(mocker, tmp_path):
    mock_capture(mocker, tmp_path)
    mocker.patch.object(android_calibrate, "_ocr_rows", return_value=[])

    with pytest.raises(android_calibrate.ConfigError, match="已预约"):
        android_calibrate.calibrate_negative_point(
            "fake", template_dir=tmp_path / "templates"
        )
    assert not (tmp_path / "templates").exists()


def test_negative_requires_detail_page(mocker, tmp_path):
    mock_capture(mocker, tmp_path)
    mocker.patch.object(
        android_calibrate,
        "_foreground_activity",
        return_value="cn.damai/.NcovSkuActivity",
    )

    with pytest.raises(android_calibrate.ConfigError, match="详情页"):
        android_calibrate.calibrate_negative_point(
            "fake", template_dir=tmp_path / "templates"
        )


def test_sku_page_saves_fixed_confirm_template(mocker, tmp_path):
    mock_capture(mocker, tmp_path)
    mocker.patch.object(
        android_calibrate,
        "_foreground_activity",
        return_value="cn.damai/.NcovSkuActivity",
    )
    mocker.patch.object(
        android_calibrate, "_ocr_rows", return_value=[(500, 1800, "确定")]
    )
    templates = tmp_path / "templates"

    result = android_calibrate.calibrate_android_point("fake", template_dir=templates)

    assert result["field"] == "confirm_point"
    assert result["point"] == "500,1800"
    assert result["template_path"] == str(templates / "sku-confirm.png")
    template = Image.open(templates / "sku-confirm.png")
    assert template.mode == "L"
    # 1000x2000 截图：半宽 190、半高 52，只裁按钮固定框
    assert template.size == (380, 120)


def test_confirm_page_saves_submit_template(mocker, tmp_path):
    mock_capture(mocker, tmp_path)
    mocker.patch.object(
        android_calibrate,
        "_foreground_activity",
        return_value="cn.damai/.ultron.view.activity.DmOrderActivity",
    )
    mocker.patch.object(
        android_calibrate, "_ocr_rows", return_value=[(500, 1800, "立即提交")]
    )
    templates = tmp_path / "templates"

    result = android_calibrate.calibrate_android_point("fake", template_dir=templates)

    assert result["field"] == "submit_point"
    assert result["point"] == "500,1800"
    assert result["template_path"] == str(templates / "sku-submit.png")
    assert (templates / "sku-submit.png").is_file()


def test_sku_confirm_template_excludes_price_block(tmp_path):
    image_path = tmp_path / "screen.png"
    image = Image.new("RGB", (1220, 2712), (255, 255, 255))
    # 左侧价格块（会被随金额撑宽），坐标与真机底部栏一致
    for x in range(40, 470):
        for y in range(2560, 2670):
            image.putpixel((x, y), (255, 0, 0))
    # 右侧确定按钮
    for x in range(737, 1183):
        for y in range(2563, 2663):
            image.putpixel((x, y), (240, 90, 30))
    image.save(image_path)

    destination = android_calibrate._save_sku_confirm_template(
        str(image_path), (960, 2613), tmp_path / "templates"
    )

    template = Image.open(destination)
    assert template.size == (462, 162)
    # 裁剪区左边界 729，价格块最右到 469；红色转灰度约 76，不应出现在模板里
    assert 76 not in template.tobytes()


def test_sku_confirm_template_is_identical_across_amounts(tmp_path):
    """价格块宽度变化不能影响确定按钮模板。"""
    def screen(price_right):
        image = Image.new("RGB", (1220, 2712), (255, 255, 255))
        for x in range(30, price_right):
            for y in range(2560, 2670):
                image.putpixel((x, y), (250, 220, 210))
        for x in range(737, 1183):
            for y in range(2563, 2663):
                image.putpixel((x, y), (240, 90, 30))
        return image

    first = tmp_path / "a.png"
    second = tmp_path / "b.png"
    screen(280).save(first)
    screen(420).save(second)

    one = android_calibrate._save_sku_confirm_template(
        str(first), (960, 2613), tmp_path / "one"
    )
    two = android_calibrate._save_sku_confirm_template(
        str(second), (960, 2613), tmp_path / "two"
    )

    assert Image.open(one).tobytes() == Image.open(two).tobytes()
