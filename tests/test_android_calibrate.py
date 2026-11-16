from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock

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
